import uuid
import json
import logging
from typing import List, Optional, Dict, Any, Tuple
from fastapi import APIRouter, Depends, UploadFile, File, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.api.dependencies import get_grading_workflow, get_db_path
from src.orchestration.workflow import GradingWorkflow
from src.db.client import create_task, update_task_status, save_grading_result, get_task, fetch_results


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


# --- Background Worker ---
async def run_grading_task(
    task_id: str, 
    files_data: List[Tuple[bytes, str]], 
    workflow: GradingWorkflow, 
    db_path: str
):
    """
    Isolated background worker with error trapping (Phase 17).
    """
    try:
        await update_task_status(db_path, task_id, "PROCESSING")
        
        # Execute Pipeline (supports multi-file/PDF flattening)
        report = await workflow.run_pipeline(files_data)
        
        # Use first filename or task_id as student_id fallback
        student_id = files_data[0][1] if files_data else task_id
        
        # Persistence
        await save_grading_result(db_path, task_id, student_id, report)
        await update_task_status(db_path, task_id, "COMPLETED")
        
    except Exception as e:
        logger.error(f"Background task {task_id} failed: {e}")
        await update_task_status(db_path, task_id, "FAILED", error=str(e))


# --- API Endpoints ---
@router.post("/grade/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("5/minute")
async def submit_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    workflow: GradingWorkflow = Depends(get_grading_workflow),
    db_path: str = Depends(get_db_path)
):
    """
    Phase 17: Asynchronous entry point for multi-file grading.
    Rate limited to 5/min to protect high-cost model compute.
    """
    task_id = str(uuid.uuid4())
    
    # Read all files into memory for background processing
    files_data = []
    for file in files:
        content = await file.read()
        files_data.append((content, file.filename))
    
    # 1. Register task in DB
    await create_task(db_path, task_id)
    
    # 2. Push to background worker
    background_tasks.add_task(run_grading_task, task_id, files_data, workflow, db_path)
    
    return TaskResponse(task_id=task_id, status="PENDING")


@router.get("/grade/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_job_status_and_results(
    request: Request,
    task_id: str,
    db_path: str = Depends(get_db_path)
):
    """
    Phase 17: Unified status and retrieval endpoint.
    Returns 206 (Partial) if still processing, or 200 with results if COMPLETED.
    Rate limited to 30/min for polling.
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
