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
    
    # 2. Serialize uploaded files (Redis broker requires JSON-compatible data)
    files_data = []
    for file in files:
        content = await file.read()
        # Convert bytes to int list for JSON serialization
        files_data.append((list(content), file.filename))
    
    # 3. Pre-persist task state (PENDING) BEFORE queueing
    await create_task(db_path, task_id)
    
    # 4. Dispatch to Celery worker queue (non-blocking)
    celery_result = grade_homework_task.apply_async(
        args=[task_id, files_data, db_path],
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
    Phase 28: Unified polling endpoint for Celery-queued tasks.
    Returns task status and results when COMPLETED.
    Rate limited to 30/min to prevent polling storms.
    """
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    response_data = {
        "task_id": task["task_id"],
        "status": task["status"],
        "error_message": task.get("error_message")
    }

    if task["status"] == "COMPLETED":
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
