import uuid
import json
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.api.dependencies import get_db_path
from src.db.client import create_task, update_task_celery_id, get_task, fetch_results
from src.worker.main import grade_homework_task


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


# --- API Endpoints (Phase 28: Celery-decoupled) ---
@router.post("/grade/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("5/minute")
async def submit_grading_job(
    request: Request,
    files: List[UploadFile] = File(...),
    db_path: str = Depends(get_db_path)
):
    """
    Phase 28: Asynchronous submission with Celery physical decoupling.
    Returns HTTP 202 immediately after queueing task to Redis.
    
    Contract Guarantees:
    - Response time < 50ms (no AI computation in HTTP lifecycle)
    - Task persisted to DB before queueing
    - Worker processes execute grading in isolated processes
    """
    # 1. Generate business task UUID
    task_id = str(uuid.uuid4())
    
    # 2. Serialize uploaded files (Phase 29: Strict scalarization for Celery JSON transport)
    files_data = []
    for file in files:
        content = await file.read()
        # Convert bytes to int list for JSON serialization
        files_data.append((list(content), str(file.filename)))  # Force str() to prevent Path objects
    
    # 3. Pre-persist task state (PENDING) BEFORE queueing
    await create_task(db_path, task_id)
    
    # 4. Dispatch to Celery worker queue (non-blocking)
    # CRITICAL: All args must be JSON-serializable primitives (str, int, list, dict)
    celery_result = grade_homework_task.apply_async(
        args=[
            str(task_id),  # Ensure string UUID
            files_data,    # List of (int_list, str) tuples
            str(db_path),  # Force string path
        ],
        task_id=task_id,  # Force business UUID as Celery task ID
    )
    
    # 5. Track Celery task ID for potential revocation
    await update_task_celery_id(db_path, task_id, celery_result.id)
    
    # 6. Immediate HTTP 202 response (physical cutoff from computation)
    logger.info(f"[API] Task {task_id} queued to Celery with ID {celery_result.id}")
    return TaskResponse(task_id=task_id, status="PENDING")


@router.get("/grade/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_job_status_and_results(
    request: Request,
    task_id: str,
    db_path: str = Depends(get_db_path)
):
    """
    Phase 29: Strengthened polling contract with progress tracking.
    
    Returns:
        - PENDING/PROCESSING: Includes progress and ETA if available
        - COMPLETED: Includes full result payload
        - FAILED: Includes sanitized error_code (not raw stack traces)
        - REJECTED: Includes rejection reason
    
    Rate limited to 30/min to prevent polling storms.
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
    
    return TaskStatusResponse(**response_data)


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
