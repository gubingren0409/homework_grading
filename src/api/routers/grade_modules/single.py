"""
Single submission grading routes.

This module handles individual student submission grading operations:
- Submit single grading job
- Query task status and results
- Get grading report
- Get task insights
- Cancel task
"""

import uuid
import json
import logging
import hashlib
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Request, Response, BackgroundTasks
from celery.exceptions import OperationalError as CeleryOperationalError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from src.api.dependencies import get_db_path, limiter
from src.api.auth import TeacherIdentity, get_current_teacher
from src.api.routers.grade_modules.helpers import (
    parse_grading_result_row as _parse_grading_result_row,
    build_paper_task_results as _build_paper_task_results,
    require_task_for_teacher as _require_task_for_teacher,
)
from src.core.config import settings
from src.db.client import (
    create_task,
    update_task_celery_id,
    get_rubric,
    set_task_rubric_id,
    _open_connection,
    aiosqlite,
)
from src.worker.main import grade_homework_task, app as celery_app
from src.core.storage_adapter import storage
from src.core.trace_context import get_trace_id
from src.api.route_helpers import (
    best_effort_cleanup_stale_pending_orphans as _best_effort_cleanup_stale_pending_orphans,
    error_detail as _error_detail,
    store_upload_file_with_limits as _store_upload_file_with_limits,
    build_task_insights as _build_task_insights,
    to_report_card as _to_report_card,
    remove_task_from_celery_queue as _remove_task_from_celery_queue,
)
from src.api.route_models import (
    TaskResponse,
    TaskStatusResponse,
    TaskReportResponse,
    TaskInsightsResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# Helper functions

def _check_redis_health() -> tuple[bool, Optional[str]]:
    """Check if Redis is reachable."""
    try:
        from src.worker.main import app as celery_app
        celery_app.backend.client.ping()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _run_task_locally(task_id: str, payload: Dict[str, Any], db_path: str, trace_id: str) -> None:
    """Execute task locally without Celery queue."""
    grade_homework_task.apply(
        args=[task_id, payload, db_path],
        task_id=task_id,
        headers={"trace_id": trace_id},
        throw=False,
    )


def _mark_local_fallback_payload(payload: Dict[str, Any], reason_detail: str | None) -> None:
    """Mark payload as local fallback with reason."""
    payload["dispatch_mode"] = "local_fallback"
    payload["fallback_reason"] = "LOCAL_FALLBACK_SINGLE_NODE_ONLY"
    if reason_detail:
        payload["fallback_detail"] = str(reason_detail)


def _dispatch_grading_task(
    *,
    task_id: str,
    payload: Dict[str, Any],
    db_path: str,
    trace_id: str,
    background_tasks: BackgroundTasks,
) -> tuple[str, str]:
    """
    Dispatch grading task to Celery queue with fallback to local execution.

    Returns:
        Tuple of (celery_task_id, dispatch_mode)
    """
    # Pre-flight: verify Redis is reachable before attempting dispatch
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
        # Fall back to local execution
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


# Routes

@router.post("/grade/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("100/minute")
async def submit_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    rubric_id: Optional[str] = Form(default=None),
    student_id: Optional[str] = Form(default=None),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    Submit single student submission for grading.

    Returns HTTP 202 immediately after queueing task.
    Files stored via storage backend before queueing.
    """
    await _best_effort_cleanup_stale_pending_orphans(db_path)

    # Generate task UUID
    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()

    # Store uploaded files
    file_refs = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)

    # Pre-persist task state (PENDING) before queueing
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

    # Dispatch to Celery worker queue
    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "single_submission"
    if student_id and student_id.strip():
        payload["student_id"] = student_id.strip()
    if bound_rubric is not None:
        payload["rubric_json"] = bound_rubric
    celery_task_id, dispatch_mode = _dispatch_grading_task(
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        trace_id=trace_id,
        background_tasks=background_tasks,
    )

    # Track Celery task ID for potential revocation
    await update_task_celery_id(task_id, celery_task_id)

    # Immediate HTTP 202 response
    logger.info(
        "task_enqueued",
        extra={
            "extra_fields": {
                "task_id": task_id,
                "event": "task_enqueued",
                "dispatch_mode": dispatch_mode,
            }
        },
    )
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=rubric_id,
        mode="single_submission",
        submitted_count=len(file_refs),
        status_endpoint=f"/api/v1/grade/{task_id}",
        stream_endpoint=f"/api/v1/tasks/{task_id}/stream",
        suggested_poll_interval_seconds=2,
    )




@router.get("/grade/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_job_status_and_results(
    request: Request,
    response: Response,
    task_id: str,
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    Get task status and results with HTTP cache negotiation.

    Supports conditional requests (If-None-Match, If-Modified-Since) to reduce
    unnecessary data transfer when task state hasn't changed.

    Returns:
        - 200 OK: Full task status (with ETag and Last-Modified headers)
        - 304 Not Modified: Task state unchanged since last request
        - 404 Not Found: Task doesn't exist
    """
    task = await _require_task_for_teacher(db_path, task_id, teacher)

    # Base response structure
    response_data = {
        "task_id": task["task_id"],
        "status": task["status"],
        "grading_status": task.get("grading_status"),
        "rubric_id": task.get("rubric_id"),
        "review_status": task.get("review_status"),
        "submitted_count": int(task.get("submitted_count") or 0),
        "fallback_reason": task.get("fallback_reason"),
        "retryable": False,
        "status_endpoint": f"/api/v1/grade/{task_id}",
        "stream_endpoint": f"/api/v1/tasks/{task_id}/stream",
        "suggested_poll_interval_seconds": 2,
    }

    if task["status"] in ["PENDING", "PROCESSING"]:
        uploaded_count = int(response_data.get("submitted_count") or 0)
        response_data.update(
            {
                "result_count": 0,
                "uploaded_count": uploaded_count,
                "processed_count": 0,
                "succeeded_count": 0,
                "rejected_count": 0,
                "progress": float(task.get("progress") or 0.0),
                "eta_seconds": int(task.get("eta_seconds") or 60),
                "next_action": "wait_for_completion",
            }
        )
        state_string = (
            f"{task['status']}_{task.get('updated_at', '')}_{task.get('error_message', '')}_"
            f"{response_data.get('submitted_count', 0)}_{response_data.get('result_count', 0)}"
        )
        etag = hashlib.md5(state_string.encode()).hexdigest()
        if request.headers.get("If-None-Match") == etag:
            response.status_code = 304
            response.headers["ETag"] = etag
            response.headers["Last-Modified"] = task.get("updated_at", "")
            return Response(status_code=304)
        response.headers["ETag"] = etag
        response.headers["Last-Modified"] = task.get("updated_at", "")
        response.headers["Cache-Control"] = "private, must-revalidate"
        return TaskStatusResponse(**response_data)

    from src.db.client import _open_connection, aiosqlite
    from src.db.dao._migrations import _ensure_paper_grading_tables

    async with _open_connection(db_path) as db:
        await _ensure_paper_grading_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                COUNT(1) AS total_results,
                SUM(CASE WHEN report_json LIKE '%"status": "REJECTED_UNREADABLE"%'
                          OR report_json LIKE '%"status":"REJECTED_UNREADABLE"%'
                         THEN 1 ELSE 0 END) AS rejected_results
            FROM grading_results WHERE task_id = ?
            """,
            (task_id,),
        ) as cursor:
            row = await cursor.fetchone()
            total_results = int((row["total_results"] if row else 0) or 0)
            rejected_results = int((row["rejected_results"] if row else 0) or 0)
        if total_results == 0:
            async with db.execute(
                """
                SELECT
                    COUNT(1) AS total_results,
                    SUM(CASE WHEN status = 'REJECTED_UNREADABLE' THEN 1 ELSE 0 END) AS rejected_results
                FROM paper_question_results WHERE task_id = ?
                """,
                (task_id,),
            ) as cursor:
                paper_row = await cursor.fetchone()
                total_results = int((paper_row["total_results"] if paper_row else 0) or 0)
                rejected_results = int((paper_row["rejected_results"] if paper_row else 0) or 0)

        uploaded_count = int(response_data.get("submitted_count") or 0)
        succeeded_results = max(0, total_results - rejected_results)
        response_data["result_count"] = total_results
        response_data["uploaded_count"] = uploaded_count
        response_data["processed_count"] = total_results
        response_data["succeeded_count"] = succeeded_results
        response_data["rejected_count"] = rejected_results

    # Status-specific enrichment
    if task["status"] == "FAILED":
        response_data["progress"] = float(task.get("progress") or 0.0)
        response_data["eta_seconds"] = 0
        raw_error = task.get("error_message", "Unknown error")
        response_data["retryable"] = True
        response_data["retry_hint"] = "retry_submit"
        response_data["next_action"] = "retry_upload"
        if "Traceback" in raw_error or "File " in raw_error:
            response_data["error_code"] = "INTERNAL_ERROR"
            response_data["error_message"] = "Internal processing error. Contact support."
        else:
            response_data["error_code"] = "TASK_FAILED"
            response_data["error_message"] = raw_error[:200]

    elif task["status"] == "COMPLETED":
        response_data["progress"] = 1.0
        response_data["eta_seconds"] = 0
        if task.get("grading_status") == "REJECTED_UNREADABLE":
            response_data["error_code"] = "INPUT_REJECTED"
            response_data["error_message"] = task.get("error_message", "Input quality too low")
            response_data["retryable"] = True
            response_data["retry_hint"] = "resubmit_with_clearer_image"
            response_data["next_action"] = "retry_upload"
        else:
            response_data["next_action"] = "view_results"

        async with _open_connection(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM grading_results WHERE task_id = ?", (task_id,)) as cursor:
                rows = await cursor.fetchall()
                results = [_parse_grading_result_row(row) for row in rows]

                if results:
                    response_data["results"] = results
                else:
                    response_data["results"] = await _build_paper_task_results(db_path, task_id)

    # Generate ETag from task state
    state_string = (
        f"{task['status']}_{task.get('updated_at', '')}_{task.get('error_message', '')}_"
        f"{response_data.get('submitted_count', 0)}_{response_data.get('result_count', 0)}"
    )
    etag = hashlib.md5(state_string.encode()).hexdigest()

    # Check If-None-Match header
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match == etag:
        response.status_code = 304
        response.headers["ETag"] = etag
        response.headers["Last-Modified"] = task.get("updated_at", "")
        return Response(status_code=304)

    # Return full payload with cache headers
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = task.get("updated_at", "")
    response.headers["Cache-Control"] = "private, must-revalidate"
    return TaskStatusResponse(**response_data)




@router.get("/grade/{task_id}/report", response_model=TaskReportResponse)
async def get_task_report(
    task_id: str,
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """Get detailed grading report for a completed task."""
    task = await _require_task_for_teacher(db_path, task_id, teacher)
    if task.get("status") != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail=_error_detail(
                error_code="TASK_NOT_COMPLETED",
                message="Task is not completed yet",
                retryable=True,
                retry_hint="wait_and_poll_status",
                next_action="wait_for_completion",
            ),
        )

    from src.db.client import _open_connection, aiosqlite, list_paper_question_results

    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, student_id, total_deduction, is_pass, report_json
            FROM grading_results
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            records = [dict(r) for r in rows]

    cards = [_to_report_card(record) for record in records]
    if not cards:
        # Fallback: try paper_question_results table
        from src.db.dao.paper import list_paper_question_results
        paper_records = await list_paper_question_results(task_id)
        cards = [_to_report_card(record) for record in paper_records]
    return TaskReportResponse(
        task_id=task_id,
        task_status=str(task.get("status") or "UNKNOWN"),
        cards=cards,
    )


@router.get("/grade/{task_id}/insights", response_model=TaskInsightsResponse)
async def get_task_insights(
    task_id: str,
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """Get task insights and statistics."""
    from src.db.client import fetch_results_by_task

    task = await _require_task_for_teacher(db_path, task_id, teacher)

    rows = await fetch_results_by_task(task_id)
    cards = [_to_report_card(record) for record in rows]
    insights = _build_task_insights(cards)
    return TaskInsightsResponse(
        task_id=task_id,
        task_status=str(task.get("status") or "UNKNOWN"),
        error_type_counts=insights["error_type_counts"],
        review_bucket_counts=insights["review_bucket_counts"],
        hotspots=insights["hotspots"],
        lecture_suggestions=insights["lecture_suggestions"],
    )


@router.post("/grade/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    Cancel a PENDING or PROCESSING task.

    Steps:
    1. Revoke the Celery task (terminate if running)
    2. Remove any queued messages from Redis
    3. Mark the task CANCELLED in DB
    4. Publish CANCELLED event via Redis PubSub for SSE listeners
    """
    from src.db.dao.tasks import update_task_status
    from src.api.sse import publish_task_status as _publish_task_status

    task = await _require_task_for_teacher(db_path, task_id, teacher)

    current_status = str(task.get("status") or "")
    if current_status not in {"PENDING", "PROCESSING"}:
        return {
            "task_id": task_id,
            "cancelled": False,
            "message": f"任务已处于终态 ({current_status})，无法取消",
            "previous_status": current_status,
        }

    # 1) Revoke Celery task
    revoked = False
    revoke_error = None
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        revoked = True
    except Exception as exc:
        revoke_error = str(exc)[:200]
        logger.warning(
            "cancel_revoke_failed",
            extra={"extra_fields": {"task_id": task_id, "error": revoke_error}},
        )

    # 2) Remove from Redis queue
    removed_count, queue_error = _remove_task_from_celery_queue(task_id)

    # 3) Mark CANCELLED in DB
    await update_task_status(
        task_id,
        "CANCELLED",
        error="教师主动取消任务",
        fallback_reason="TEACHER_CANCELLED",
    )

    # 4) Publish CANCELLED event for SSE
    try:
        await _publish_task_status(task_id, "CANCELLED", progress=1.0, eta_seconds=0)
    except Exception:
        pass  # best-effort

    logger.info(
        "task_cancelled",
        extra={
            "extra_fields": {
                "event": "task_cancelled",
                "task_id": task_id,
                "previous_status": current_status,
                "revoked": revoked,
                "removed_from_queue": removed_count,
            }
        },
    )

    return {
        "task_id": task_id,
        "cancelled": True,
        "previous_status": current_status,
        "revoked": revoked,
        "removed_from_queue": removed_count,
        "message": "任务已取消",
    }

