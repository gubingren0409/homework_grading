import uuid
import json
import logging
import hashlib
import math
import asyncio
import tempfile
import mimetypes
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Request, Response, BackgroundTasks
from fastapi.responses import FileResponse
from celery.exceptions import OperationalError as CeleryOperationalError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from src.api.dependencies import get_db_path, limiter
from src.api.sse import create_sse_response
from src.api.auth import TeacherIdentity, get_current_teacher
from src.core.config import settings
from src.db.client import (
    create_task,
    update_task_celery_id,
    set_task_rubric_id,
    get_task,
    fetch_results,
    fetch_results_by_task,
    save_rubric,
    get_rubric,
    list_rubrics,
    get_recent_rubric_by_fingerprint,
    append_rubric_generate_audit,
)
from src.worker.main import grade_homework_task
from src.core.storage_adapter import storage
from src.core.trace_context import get_trace_id
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.schemas.rubric_ir import TeacherRubric
from src.core.exceptions import GradingSystemError
from src.api.route_helpers import (
    best_effort_cleanup_stale_pending_orphans as _best_effort_cleanup_stale_pending_orphans,
    compute_source_fingerprint as _compute_source_fingerprint,
    derive_student_ids_from_filenames as _derive_student_ids_from_filenames,
    error_detail as _error_detail,
    request_client_ip as _request_client_ip,
    store_upload_file_with_limits as _store_upload_file_with_limits,
    build_task_insights as _build_task_insights,
    to_report_card as _to_report_card,
    validate_batch_single_page_file as _validate_batch_single_page_file,
)
from src.api.route_models import (
    GradeFlowGuideResponse,
    GradingResultItem,
    ReportCardItem,
    ReportDeductionItem,
    TaskHistoryItem,
    TaskHistoryResponse,
    TaskInsightHotspotItem,
    TaskInsightsResponse,
    TaskReportResponse,
    TaskResponse,
    TaskStatusResponse,
    LectureSuggestionItem,
)


logger = logging.getLogger(__name__)
router = APIRouter()

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
    try:
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
        background_tasks.add_task(_run_task_locally, task_id, payload, db_path, trace_id)
        return f"local:{task_id}", "local_fallback"


@router.post("/grade/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("100/minute")  # Phase 35: Increased for batch grading (100+ students)
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
    Phase 32: Storage Adapter Pattern - Backend-agnostic file handling.
    Phase 35: Batch-friendly rate limit (100/min) for large-scale grading.
    
    Returns HTTP 202 immediately after queueing task reference to Redis.
    File content stored via storage backend (LocalStorage/S3Storage).
    
    Contract Guarantees:
    - Response time < 50ms (no AI computation in HTTP lifecycle)
    - Task persisted to DB before queueing
    - Files written to storage backend before queueing
    - Worker receives file URIs (NOT file content)
    """
    await _best_effort_cleanup_stale_pending_orphans(db_path)

    # 1. Generate business task UUID
    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    
    # 2. Store uploaded files via storage adapter (Phase 32)
    file_refs = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)
    
    # 3. Pre-persist task state (PENDING) BEFORE queueing
    await create_task(db_path, task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    bound_rubric: Optional[Dict[str, Any]] = None
    if rubric_id:
        rubric_row = await get_rubric(db_path, rubric_id)
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
        await set_task_rubric_id(db_path, task_id, rubric_id)
    
    # 4. Dispatch to Celery worker queue (non-blocking)
    # Phase 32: Storage Adapter - enqueue URIs (backend-agnostic)
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

    # 5. Track Celery task ID for potential revocation
    await update_task_celery_id(db_path, task_id, celery_task_id)
    
    # 6. Immediate HTTP 202 response (physical cutoff from computation)
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

    await create_task(db_path, task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    bound_rubric: Optional[Dict[str, Any]] = None
    if rubric_id:
        rubric_row = await get_rubric(db_path, rubric_id)
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
        await set_task_rubric_id(db_path, task_id, rubric_id)

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

    await update_task_celery_id(db_path, task_id, celery_task_id)
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

    await create_task(db_path, task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

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

    await update_task_celery_id(db_path, task_id, celery_task_id)
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


@router.get("/grade/flow-guide", response_model=GradeFlowGuideResponse)
async def get_grade_flow_guide():
    """
    学生任务台对接辅助接口：
    提供提交、轮询、SSE 与错误处理的最小状态机约定。
    """
    return GradeFlowGuideResponse(
        submit_endpoint="/api/v1/grade/submit",
        batch_submit_endpoint="/api/v1/grade/submit-batch",
        batch_submit_with_reference_endpoint="/api/v1/grade/submit-batch-with-reference",
        status_endpoint_template="/api/v1/grade/{task_id}",
        stream_endpoint_template="/api/v1/tasks/{task_id}/stream",
        task_status_enum=["PENDING", "PROCESSING", "COMPLETED", "FAILED"],
        terminal_statuses=["COMPLETED", "FAILED"],
        error_code_actions={
            "UPLOAD_TIMEOUT": "retry_upload",
            "FILE_TOO_LARGE": "adjust_file",
            "RATE_LIMITED": "wait_and_retry",
            "TASK_NOT_FOUND": "submit_new_task",
            "TASK_NOT_COMPLETED": "wait_for_completion",
            "INPUT_REJECTED": "retry_upload",
            "BATCH_FILE_TYPE_UNSUPPORTED": "adjust_file",
            "TASK_FAILED": "retry_upload",
            "INTERNAL_ERROR": "contact_support",
            "SSE_BACKEND_UNAVAILABLE": "fallback_to_polling",
            "UPSTREAM_UNAVAILABLE": "retry_upload",
        },
        notes=[
            "优先使用 SSE，若出现 SSE_BACKEND_UNAVAILABLE 则回退到状态轮询。",
            "状态轮询建议间隔 2 秒；可配合 ETag 条件请求降低带宽。",
            "FAILED 与 REJECTED_UNREADABLE 默认允许重新提交。",
            "队列后端不可用时，会自动回退到本地后台执行并继续返回 task_id。",
            "submit_endpoint 用于单学生提交（可多页）；batch_submit_endpoint 用于多学生单页批处理。",
            "Rubric 生成走整页感知聚合链路，不依赖布局切片 gate。",
        ],
    )


@router.get("/grade/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_job_status_and_results(
    request: Request,
    response: Response,
    task_id: str,
    db_path: str = Depends(get_db_path)
):
    """
    Phase 32: HTTP cache negotiation with ETag/Last-Modified headers.
    
    Supports conditional requests (If-None-Match, If-Modified-Since) to reduce
    unnecessary data transfer when task state hasn't changed.
    
    Returns:
        - 200 OK: Full task status (with ETag and Last-Modified headers)
        - 304 Not Modified: Task state unchanged since last request
        - 404 Not Found: Task doesn't exist
        
    Benefits:
        - Eliminates duplicate payload transfer (200 OK → 304 Not Modified)
        - Reduces bandwidth by ~70% for unchanged status polls
        - Client can safely poll at higher frequency
    
    Rate limited to 30/min to prevent polling storms (prefer SSE for real-time).
    """
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )
    
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

    from src.db.client import _open_connection, aiosqlite

    async with _open_connection(db_path) as db:
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
        # P8.5-04: 三段计数语义统一
        # uploaded = 教师提交份数；processed = 已落库结果数（含拒判占位）；
        # succeeded = 已落库结果中非拒判部分；rejected = 拒判数。
        uploaded_count = int(response_data.get("submitted_count") or 0)
        succeeded_results = max(0, total_results - rejected_results)
        response_data["result_count"] = total_results
        response_data["uploaded_count"] = uploaded_count
        response_data["processed_count"] = total_results
        response_data["succeeded_count"] = succeeded_results
        response_data["rejected_count"] = rejected_results

    # Phase 29: Status-specific enrichment
    if task["status"] in ["PENDING", "PROCESSING"]:
        response_data["progress"] = float(task.get("progress") or 0.0)
        eta_value = task.get("eta_seconds")
        response_data["eta_seconds"] = int(eta_value) if eta_value is not None else 60
        response_data["next_action"] = "wait_for_completion"
    
    elif task["status"] == "FAILED":
        response_data["progress"] = float(task.get("progress") or 0.0)
        response_data["eta_seconds"] = 0
        # Sanitize error messages: strip internal stack traces
        raw_error = task.get("error_message", "Unknown error")
        response_data["retryable"] = True
        response_data["retry_hint"] = "retry_submit"
        response_data["next_action"] = "retry_upload"
        if "Traceback" in raw_error or "File " in raw_error:
            response_data["error_code"] = "INTERNAL_ERROR"
            response_data["error_message"] = "Internal processing error. Contact support."
        else:
            response_data["error_code"] = "TASK_FAILED"
            response_data["error_message"] = raw_error[:200]  # Truncate long errors
    
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
        # Retrieve results associated with this task
        async with _open_connection(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM grading_results WHERE task_id = ?", (task_id,)) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    # Try to parse report_json back into a dict for cleaner API output
                    try:
                        item["report_json"] = json.loads(item["report_json"])
                    except:
                        pass
                    results.append(item)
                response_data["results"] = results
    
    # Phase 32: Generate ETag from task state
    # ETag = hash(status + updated_at + error_message)
    state_string = (
        f"{task['status']}_{task.get('updated_at', '')}_{task.get('error_message', '')}_"
        f"{response_data.get('submitted_count', 0)}_{response_data.get('result_count', 0)}"
    )
    etag = hashlib.md5(state_string.encode()).hexdigest()
    
    # Check If-None-Match header (conditional request)
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match == etag:
        # Task state unchanged - return 304 Not Modified
        response.status_code = 304
        response.headers["ETag"] = etag
        response.headers["Last-Modified"] = task.get("updated_at", "")
        return Response(status_code=304)
    
    # Task state changed - return full payload with cache headers
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = task.get("updated_at", "")
    response.headers["Cache-Control"] = "private, must-revalidate"
    
    return TaskStatusResponse(**response_data)


@router.get("/grade-batch/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_batch_job_status_and_results(
    request: Request,
    response: Response,
    task_id: str,
    db_path: str = Depends(get_db_path),
):
    return await get_job_status_and_results(
        request=request,
        response=response,
        task_id=task_id,
        db_path=db_path,
    )


@router.get("/tasks/{task_id}/stream")
async def stream_task_status(
    task_id: str,
    db_path: str = Depends(get_db_path)
):
    """
    Phase 32: Server-Sent Events (SSE) - Real-time task status push.
    
    Replaces polling with long-lived HTTP connection that receives status updates.
    Automatically closes when task reaches terminal state (COMPLETED/FAILED).
    
    Client Usage (JavaScript):
        const eventSource = new EventSource('/api/v1/tasks/{task_id}/stream');
        
        eventSource.addEventListener('status_update', (event) => {
            const data = JSON.parse(event.data);
            updateUI(data.status, data.progress);
        });
        
        eventSource.addEventListener('complete', (event) => {
            eventSource.close();
            fetchFullResults(task_id);
        });
    
    Benefits:
        - Eliminates polling overhead (30 req/min → 1 connection)
        - Sub-second status updates (vs 2s polling interval)
        - Standard SSE protocol (browser-native support)
    
    Returns:
        EventSourceResponse with text/event-stream content-type
    """
    # Verify task exists before opening SSE stream
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )
    
    return create_sse_response(db_path, task_id)


@router.get("/results", response_model=List[GradingResultItem])
async def get_all_results(
    task_id: Optional[str] = Query(default=None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db_path: str = Depends(get_db_path)
):
    """Paginated result retrieval."""
    if task_id:
        task = await get_task(db_path, task_id)
        if not task:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    error_code="TASK_NOT_FOUND",
                    message="Task not found",
                    retryable=False,
                    next_action="submit_new_task",
                ),
            )
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
    offset = (page - 1) * limit
    if task_id:
        from src.db.client import _open_connection, aiosqlite

        async with _open_connection(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, student_id, total_deduction, is_pass, report_json
                FROM grading_results
                WHERE task_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (task_id, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                results = [dict(r) for r in rows]
    else:
        results = await fetch_results(db_path, limit, offset)
    return [GradingResultItem(**r) for r in results]


@router.get("/results/{result_id}/inputs/{index}")
async def get_result_input_asset(
    result_id: int,
    index: int,
    db_path: str = Depends(get_db_path),
):
    from src.db.client import _open_connection, aiosqlite

    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT report_json FROM grading_results WHERE id = ?",
            (result_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="result not found")

    try:
        payload = json.loads(row["report_json"])
    except Exception:
        payload = {}

    file_refs = payload.get("input_file_refs") if isinstance(payload.get("input_file_refs"), list) else []
    if index < 0 or index >= len(file_refs):
        raise HTTPException(status_code=404, detail="input asset not found")

    file_ref = str(file_refs[index] or "").strip()
    parsed = urlparse(file_ref)
    if parsed.scheme != "file":
        raise HTTPException(status_code=400, detail="unsupported input asset scheme")

    raw_path = unquote(parsed.path or "")
    if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    asset_path = Path(raw_path).resolve()
    uploads_root = settings.uploads_path.resolve()
    try:
        asset_path.relative_to(uploads_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="input asset path rejected") from exc
    if not asset_path.exists():
        raise HTTPException(status_code=410, detail="input asset expired (TTL cleanup)")

    media_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
    return FileResponse(asset_path, media_type=media_type, filename=asset_path.name)


@router.get("/tasks/history", response_model=TaskHistoryResponse)
async def get_task_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(default=None, pattern="^(PENDING|PROCESSING|COMPLETED|FAILED)$"),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    offset = (page - 1) * limit
    where_conditions: List[str] = []
    params: List[Any] = []
    if status:
        where_conditions.append("t.status = ?")
        params.append(status)
    if settings.auth_enabled:
        where_conditions.append("t.teacher_id = ?")
        params.append(teacher.teacher_id)
    where_clause = ("WHERE " + " AND ".join(where_conditions)) if where_conditions else ""
    params.extend([limit, offset])

    from src.db.client import _open_connection, aiosqlite

    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT
                t.task_id,
                t.status,
                t.grading_status,
                t.review_status,
                t.rubric_id,
                t.submitted_count,
                t.progress,
                t.created_at,
                t.updated_at,
                COALESCE(COUNT(gr.id), 0) AS result_count
            FROM tasks t
            LEFT JOIN grading_results gr ON gr.task_id = t.task_id
            {where_clause}
            GROUP BY t.task_id
            ORDER BY t.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        ) as cursor:
            rows = await cursor.fetchall()
            items = [TaskHistoryItem(**dict(r)) for r in rows]
    return TaskHistoryResponse(page=page, limit=limit, items=items)


@router.get("/grade/{task_id}/report", response_model=TaskReportResponse)
async def get_task_report(
    task_id: str,
    db_path: str = Depends(get_db_path),
):
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )
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

    from src.db.client import _open_connection, aiosqlite

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
    return TaskReportResponse(
        task_id=task_id,
        task_status=str(task.get("status") or "UNKNOWN"),
        cards=cards,
    )


@router.get("/grade/{task_id}/insights", response_model=TaskInsightsResponse)
async def get_task_insights(
    task_id: str,
    db_path: str = Depends(get_db_path),
):
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )

    rows = await fetch_results_by_task(db_path, task_id)
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


