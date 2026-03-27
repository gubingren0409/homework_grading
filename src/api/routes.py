import uuid
import json
import logging
import hashlib
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Request, Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.api.dependencies import get_db_path
from src.api.sse import create_sse_response
from src.db.client import create_task, update_task_celery_id, get_task, fetch_results
from src.worker.main import grade_homework_task
from src.core.storage_adapter import storage
from src.core.trace_context import get_trace_id
from src.worker.main import emit_trace_probe


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["grading"])
limiter = Limiter(key_func=get_remote_address)


# --- Schemas ---
class TaskResponse(BaseModel):
    task_id: str
    status: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    error_message: Optional[str] = None
    error_code: Optional[str] = None  # Phase 29: Sanitized error codes
    progress: Optional[float] = None  # Phase 29: Progress percentage (0.0-1.0)
    eta_seconds: Optional[int] = None  # Phase 29: Estimated time to completion
    results: Optional[List[Dict[str, Any]]] = None

class GradingResultItem(BaseModel):
    id: int
    student_id: Optional[str]
    total_deduction: float
    is_pass: bool
    report_json: str


class TraceProbeResponse(BaseModel):
    trace_id: str
    task_id: str
    celery_task_id: str
    status: str


# --- API Endpoints (Phase 28: Celery-decoupled) ---
@router.post("/grade/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("5/minute")
async def submit_grading_job(
    request: Request,
    files: List[UploadFile] = File(...),
    db_path: str = Depends(get_db_path)
):
    """
    Phase 32: Storage Adapter Pattern - Backend-agnostic file handling.
    
    Returns HTTP 202 immediately after queueing task reference to Redis.
    File content stored via storage backend (LocalStorage/S3Storage).
    
    Contract Guarantees:
    - Response time < 50ms (no AI computation in HTTP lifecycle)
    - Task persisted to DB before queueing
    - Files written to storage backend before queueing
    - Worker receives file URIs (NOT file content)
    """
    # 1. Generate business task UUID
    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    
    # 2. Store uploaded files via storage adapter (Phase 32)
    file_refs = []
    for file in files:
        content = await file.read()
        # Storage backend writes and returns URI (file:// or s3://)
        file_ref = storage.store_file(task_id, content, file.filename)
        file_refs.append(file_ref)
    
    # 3. Pre-persist task state (PENDING) BEFORE queueing
    await create_task(db_path, task_id)
    
    # 4. Dispatch to Celery worker queue (non-blocking)
    # Phase 32: Storage Adapter - enqueue URIs (backend-agnostic)
    payload = storage.prepare_payload(file_refs)
    celery_result = grade_homework_task.apply_async(
        args=[task_id, payload, db_path],
        task_id=task_id,
        headers={"trace_id": trace_id},
    )
    
    # 5. Track Celery task ID for potential revocation
    await update_task_celery_id(db_path, task_id, celery_result.id)
    
    # 6. Immediate HTTP 202 response (physical cutoff from computation)
    logger.info(
        "task_enqueued",
        extra={"extra_fields": {"task_id": task_id, "event": "task_enqueued"}},
    )
    return TaskResponse(task_id=task_id, status="PENDING")


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
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Base response structure
    response_data = {
        "task_id": task["task_id"],
        "status": task["status"],
    }
    
    # Phase 29: Status-specific enrichment
    if task["status"] in ["PENDING", "PROCESSING"]:
        # TODO: Implement progress tracking via Redis/Celery backend
        response_data["progress"] = 0.5 if task["status"] == "PROCESSING" else 0.0
        response_data["eta_seconds"] = 45  # Placeholder: avg grading time
    
    elif task["status"] == "FAILED":
        # Sanitize error messages: strip internal stack traces
        raw_error = task.get("error_message", "Unknown error")
        if "Traceback" in raw_error or "File " in raw_error:
            response_data["error_code"] = "INTERNAL_ERROR"
            response_data["error_message"] = "Internal processing error. Contact support."
        else:
            response_data["error_code"] = "TASK_FAILED"
            response_data["error_message"] = raw_error[:200]  # Truncate long errors
    
    elif task["status"] == "REJECTED":
        response_data["error_code"] = "INPUT_REJECTED"
        response_data["error_message"] = task.get("error_message", "Input quality too low")

    elif task["status"] == "COMPLETED":
        # Retrieve results associated with this task
        from src.db.client import _open_connection, aiosqlite
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
    state_string = f"{task['status']}_{task.get('updated_at', '')}_{task.get('error_message', '')}"
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


@router.get("/tasks/{task_id}/stream")
async def stream_task_status(
    task_id: str,
    db_path: str = Depends(get_db_path)
):
    """
    Phase 32: Server-Sent Events (SSE) - Real-time task status push.
    
    Replaces polling with long-lived HTTP connection that receives status updates.
    Automatically closes when task reaches terminal state (COMPLETED/FAILED/REJECTED).
    
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
        raise HTTPException(status_code=404, detail="Task not found")
    
    return create_sse_response(db_path, task_id)


@router.get("/results", response_model=List[GradingResultItem])
async def get_all_results(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db_path: str = Depends(get_db_path)
):
    """Paginated result retrieval."""
    offset = (page - 1) * limit
    results = await fetch_results(db_path, limit, offset)
    return [GradingResultItem(**r) for r in results]


@router.post("/trace/probe", response_model=TraceProbeResponse)
async def trace_probe():
    """
    Phase 34 observability probe endpoint.
    Uses Celery headers + contextvars path without touching business kwargs.
    """
    trace_id = get_trace_id()
    task_id = f"probe-{uuid.uuid4()}"
    result = emit_trace_probe.apply_async(args=[task_id], headers={"trace_id": trace_id})
    logger.info(
        "trace_probe_enqueued",
        extra={"extra_fields": {"task_id": task_id, "event": "trace_probe_enqueued"}},
    )
    return TraceProbeResponse(
        trace_id=trace_id,
        task_id=task_id,
        celery_task_id=result.id,
        status="ENQUEUED",
    )
