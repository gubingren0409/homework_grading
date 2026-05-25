"""
Batch grading routes.

This module handles batch grading operations:
- Submit batch grading job (multiple single-page submissions)
- Submit batch with reference (auto-generate rubric + grade)
- Query batch job status
"""

import uuid
import json
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request, Response, BackgroundTasks
from celery.exceptions import OperationalError as CeleryOperationalError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from src.api.dependencies import get_db_path, limiter
from src.api.auth import TeacherIdentity, get_current_teacher
from src.core.config import settings
from src.db.client import (
    create_task,
    update_task_celery_id,
    get_rubric,
    set_task_rubric_id,
)
from src.worker.main import grade_homework_task, app as celery_app
from src.core.storage_adapter import storage
from src.core.trace_context import get_trace_id
from src.api.route_helpers import (
    best_effort_cleanup_stale_pending_orphans as _best_effort_cleanup_stale_pending_orphans,
    error_detail as _error_detail,
    store_upload_file_with_limits as _store_upload_file_with_limits,
    derive_student_ids_from_filenames as _derive_student_ids_from_filenames,
    validate_batch_single_page_file as _validate_batch_single_page_file,
)
from src.api.route_models import TaskResponse, TaskStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter()
_LOCAL_FALLBACK_REASON = "LOCAL_FALLBACK_SINGLE_NODE_ONLY"


def _check_redis_health() -> tuple[bool, str]:
    """Quick Redis ping with instance identification for dual-Redis debugging."""
    target = f"{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
    try:
        import redis as _redis
        client = _redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        logger.debug(f"Redis health OK at {target}")
        return True, ""
    except Exception as exc:
        logger.warning(f"Redis health FAIL at {target}: {exc}")
        return False, str(exc)[:200]


def _mark_local_fallback_payload(payload: Dict[str, Any], reason_detail: str | None) -> None:
    payload["dispatch_mode"] = "local_fallback"
    payload["fallback_reason"] = _LOCAL_FALLBACK_REASON
    if reason_detail:
        payload["fallback_reason_detail"] = reason_detail


def _run_task_locally(task_id: str, payload: Dict[str, Any], db_path: str, trace_id: str) -> None:
    grade_homework_task.apply(
        args=[task_id, payload, db_path],
        task_id=task_id,
        headers={"trace_id": trace_id},
        throw=False,
    )


def _dispatch_grading_task(
    *,
    task_id: str,
    payload: Dict[str, Any],
    db_path: str,
    trace_id: str,
    background_tasks: BackgroundTasks,
) -> tuple[str, str]:
    # Pre-flight: verify Redis is reachable before attempting dispatch.
    # Without this, a down Redis may cause silent message loss.
    redis_ok, redis_err = _check_redis_health()
    if not redis_ok:
        if not settings.allow_local_task_fallback:
            raise HTTPException(
                status_code=503,
                detail=_error_detail(
                    error_code="QUEUE_UNAVAILABLE",
                    message=(
                        "Redis unavailable and local fallback disabled. "
                        "Single-node local fallback is only safe on one API instance."
                    ),
                    retryable=True,
                    retry_hint="retry_submit",
                    next_action="restore_queue_or_enable_local_fallback",
                ),
            )
        logger.warning(
            "queue_dispatch_redis_unreachable",
            extra={
                "extra_fields": {
                    "task_id": task_id,
                    "event": "queue_dispatch_redis_unreachable",
                    "reason": redis_err,
                }
            },
        )
        # Fall back to local execution rather than dropping the task
        _mark_local_fallback_payload(payload, redis_err)
        background_tasks.add_task(_run_task_locally, task_id, payload, db_path, trace_id)
        return f"local:{task_id}", "local_fallback"

    try:
        payload["dispatch_mode"] = "celery"
        celery_result = grade_homework_task.apply_async(
            args=[task_id, payload, db_path],
            task_id=task_id,
            headers={"trace_id": trace_id},
            retry=False,
            ignore_result=True,
        )
        return celery_result.id, "celery"
    except (CeleryOperationalError, KombuOperationalError, RedisError, TimeoutError) as exc:
        logger.warning(
            "queue_dispatch_fallback_local",
            extra={
                "extra_fields": {
                    "task_id": task_id,
                    "event": "queue_dispatch_fallback_local",
                    "reason": str(exc),
                }
            },
        )
        if not settings.allow_local_task_fallback:
            raise HTTPException(
                status_code=503,
                detail=_error_detail(
                    error_code="QUEUE_UNAVAILABLE",
                    message=(
                        "Queue dispatch failed and local fallback is disabled. "
                        "Single-node local fallback is only safe on one API instance."
                    ),
                    retryable=True,
                    retry_hint="retry_submit",
                    next_action="restore_queue_or_enable_local_fallback",
                ),
            )
        _mark_local_fallback_payload(payload, str(exc))
        background_tasks.add_task(_run_task_locally, task_id, payload, db_path, trace_id)
        return f"local:{task_id}", "local_fallback"


@router.post("/grade/submit-batch", response_model=TaskResponse, status_code=202)
@limiter.limit("60/minute")
async def submit_batch_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    rubric_id: Optional[str] = Form(default=None),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    Batch mode:
    - one uploaded image == one student submission
    - each file is graded independently under the same task_id
    """
    del request
    if not files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No files provided for batch grading.",
                retryable=False,
                next_action="adjust_file",
            ),
        )

    for upload in files:
        _validate_batch_single_page_file(upload)

    await _best_effort_cleanup_stale_pending_orphans(db_path)

    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    student_ids = _derive_student_ids_from_filenames(files)

    file_refs: List[str] = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)

    await create_task(task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    bound_rubric: Optional[Dict[str, Any]] = None
    if rubric_id:
        rubric_row = await get_rubric(rubric_id)
        if not rubric_row:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    error_code="RUBRIC_NOT_FOUND",
                    message="Rubric not found",
                    retryable=False,
                    next_action="select_valid_rubric",
                ),
            )
        rubric_raw = rubric_row.get("rubric_json")
        bound_rubric = json.loads(rubric_raw) if isinstance(rubric_raw, str) else rubric_raw
        await set_task_rubric_id(task_id, rubric_id)

    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "batch_single_page"
    payload["student_ids"] = student_ids
    if bound_rubric is not None:
        payload["rubric_json"] = bound_rubric

    celery_task_id, dispatch_mode = _dispatch_grading_task(
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        trace_id=trace_id,
        background_tasks=background_tasks,
    )

    await update_task_celery_id(task_id, celery_task_id)
    logger.info(
        "task_enqueued",
        extra={
            "extra_fields": {
                "task_id": task_id,
                "event": "task_enqueued_batch",
                "dispatch_mode": dispatch_mode,
            }
        },
    )
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=rubric_id,
        mode="batch_single_page",
        submitted_count=len(file_refs),
        status_endpoint=f"/api/v1/grade/{task_id}",
        stream_endpoint=f"/api/v1/tasks/{task_id}/stream",
        suggested_poll_interval_seconds=2,
    )


@router.post("/grade/submit-batch-with-reference", response_model=TaskResponse, status_code=202)
@limiter.limit("40/minute")
async def submit_batch_with_reference_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    reference_files: List[UploadFile] = File(...),
    files: List[UploadFile] = File(...),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    One-shot smart batch mode:
    - upload reference + student answers in one request
    - worker auto-generates rubric and grades in a single task
    """
    del request
    if not reference_files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No reference files provided.",
                retryable=False,
                next_action="adjust_file",
            ),
        )
    if not files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No student files provided for batch grading.",
                retryable=False,
                next_action="adjust_file",
            ),
        )

    for upload in files:
        _validate_batch_single_page_file(upload)

    await _best_effort_cleanup_stale_pending_orphans(db_path)

    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    student_ids = _derive_student_ids_from_filenames(files)

    file_refs: List[str] = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)

    reference_file_refs: List[str] = []
    for file in reference_files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        reference_file_refs.append(file_ref)

    await create_task(task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "batch_single_page"
    payload["student_ids"] = student_ids
    payload["reference_file_refs"] = reference_file_refs

    celery_task_id, dispatch_mode = _dispatch_grading_task(
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        trace_id=trace_id,
        background_tasks=background_tasks,
    )

    await update_task_celery_id(task_id, celery_task_id)
    logger.info(
        "task_enqueued",
        extra={
            "extra_fields": {
                "task_id": task_id,
                "event": "task_enqueued_batch_with_reference",
                "dispatch_mode": dispatch_mode,
            }
        },
    )
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=None,
        mode="batch_single_page",
        submitted_count=len(file_refs),
        status_endpoint=f"/api/v1/grade/{task_id}",
        stream_endpoint=f"/api/v1/tasks/{task_id}/stream",
        suggested_poll_interval_seconds=2,
    )


@router.get("/grade-batch/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_batch_job_status_and_results(
    request: Request,
    response: Response,
    task_id: str,
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    Query batch job status and results.
    This is a convenience endpoint that delegates to the single job status endpoint.
    """
    # Import here to avoid circular dependency
    from src.api.routers.grade_modules.single import get_job_status_and_results

    return await get_job_status_and_results(
        request=request,
        response=response,
        task_id=task_id,
        db_path=db_path,
        teacher=teacher,
    )
