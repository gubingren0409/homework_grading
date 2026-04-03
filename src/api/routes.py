import uuid
import json
import logging
import hashlib
import math
import asyncio
import tempfile
from typing import List, Optional, Dict, Any, Literal

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.api.dependencies import get_db_path
from src.api.sse import create_sse_response
from src.db.client import (
    create_task,
    update_task_celery_id,
    set_task_rubric_id,
    get_task,
    fetch_results,
    list_pending_review_tasks,
    save_rubric,
    get_rubric,
    list_rubrics,
    list_hygiene_interceptions,
    get_hygiene_interception_by_id,
    update_hygiene_interception_action,
    bulk_update_hygiene_interception_action,
    create_golden_annotation_asset,
    list_golden_annotation_assets,
)
from src.worker.main import grade_homework_task
from src.core.storage_adapter import storage
from src.core.trace_context import get_trace_id
from src.worker.main import emit_trace_probe
from src.perception.factory import create_perception_engine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.schemas.rubric_ir import TeacherRubric
from src.core.config import settings
from src.skills.service import SkillService
from src.skills.interfaces import ValidationInput


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["grading"])
limiter = Limiter(key_func=get_remote_address)


async def _store_upload_file_with_limits(task_id: str, upload: UploadFile) -> str:
    filename = upload.filename or "upload.bin"
    total_bytes = 0
    try:
        with tempfile.SpooledTemporaryFile(
            max_size=settings.upload_spool_max_size_bytes,
            mode="w+b",
        ) as spool:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        upload.read(settings.upload_chunk_size_bytes),
                        timeout=settings.request_body_read_timeout_seconds,
                    )
                except asyncio.TimeoutError as exc:
                    raise HTTPException(status_code=408, detail="upload read timeout") from exc
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > settings.max_request_body_bytes:
                    raise HTTPException(status_code=413, detail="file too large")
                spool.write(chunk)
            spool.seek(0)
            return storage.store_fileobj(task_id, spool, filename)
    finally:
        await upload.close()


# --- Schemas ---
class TaskResponse(BaseModel):
    task_id: str
    status: str
    rubric_id: Optional[str] = None

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    grading_status: Optional[str] = None
    rubric_id: Optional[str] = None
    review_status: Optional[str] = None
    fallback_reason: Optional[str] = None
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


class PendingReviewTaskItem(BaseModel):
    task_id: str
    status: str
    grading_status: Optional[str] = None
    rubric_id: Optional[str] = None
    review_status: str
    error_message: Optional[str] = None
    fallback_reason: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ReviewFlowGuideResponse(BaseModel):
    pending_list_endpoint: str
    task_status_enum: List[str]
    grading_status_enum: List[str]
    notes: List[str]


class RubricGenerateResponse(BaseModel):
    rubric_id: str
    question_id: Optional[str] = None
    grading_points_count: int


class RubricSummaryItem(BaseModel):
    rubric_id: str
    question_id: Optional[str] = None
    created_at: Optional[str] = None


class RubricDetailResponse(BaseModel):
    rubric_id: str
    question_id: Optional[str] = None
    created_at: Optional[str] = None
    rubric_json: Dict[str, Any]


class HygieneInterceptionItem(BaseModel):
    id: int
    trace_id: str
    task_id: Optional[str] = None
    interception_node: str
    raw_image_path: Optional[str] = None
    action: str
    created_at: Optional[str] = None


class HygieneActionUpdateRequest(BaseModel):
    action: str


class HygieneBulkActionUpdateRequest(BaseModel):
    record_ids: List[int]
    action: str


class BoundingBoxInput(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class AnnotationFeedbackRequest(BaseModel):
    task_id: str
    region_id: str
    region_type: Literal["question_region", "answer_region"]
    image_width: int = Field(..., gt=0)
    image_height: int = Field(..., gt=0)
    bbox: BoundingBoxInput
    teacher_text_feedback: str = Field(..., min_length=1)
    expected_score: float = Field(..., ge=0.0)
    perception_ir_snapshot: Dict[str, Any]
    cognitive_ir_snapshot: Dict[str, Any]
    is_integrated_to_dataset: bool = False


class AnnotationFeedbackResponse(BaseModel):
    status: str
    trace_id: str
    task_id: str
    region_id: str


class GoldenAnnotationAssetItem(BaseModel):
    id: int
    trace_id: str
    task_id: str
    region_id: str
    region_type: str
    image_width: int
    image_height: int
    bbox_coordinates: List[float]
    teacher_text_feedback: str
    expected_score: float
    is_integrated_to_dataset: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SkillLayoutParseRequest(BaseModel):
    image_base64: str = Field(..., min_length=1)
    context_type: str = Field(default="STUDENT_ANSWER")
    page_index: int = Field(default=0, ge=0)
    target_question_no: Optional[str] = None


class SkillValidationRequest(BaseModel):
    task_id: str
    question_id: Optional[str] = None
    perception_payload: Dict[str, Any]
    evaluation_payload: Dict[str, Any]
    rubric_payload: Optional[Dict[str, Any]] = None


def _validate_bbox_in_image_space(*, bbox: BoundingBoxInput, image_width: int, image_height: int) -> List[float]:
    if image_width <= 0 or image_height <= 0:
        raise HTTPException(status_code=422, detail="image_width and image_height must be positive")

    coords = [bbox.x1, bbox.y1, bbox.x2, bbox.y2]
    if not all(math.isfinite(v) for v in coords):
        raise HTTPException(status_code=422, detail="bbox contains non-finite values")

    x1, y1, x2, y2 = coords
    if x2 < x1 or y2 < y1:
        raise HTTPException(status_code=422, detail="bbox must satisfy x2>=x1 and y2>=y1")
    if x1 < 0 or y1 < 0 or x2 > image_width or y2 > image_height:
        raise HTTPException(status_code=422, detail="bbox out of image bounds")
    return [x1, y1, x2, y2]


def _extract_layout_region_from_snapshot(perception_ir_snapshot: Dict[str, Any], region_id: str) -> Optional[Dict[str, Any]]:
    regions = perception_ir_snapshot.get("regions")
    if not isinstance(regions, list):
        return None
    for item in regions:
        if isinstance(item, dict) and str(item.get("target_id")) == region_id:
            return item
    return None


def _bbox_from_snapshot_region(region: Dict[str, Any], *, image_width: int, image_height: int) -> List[float]:
    bbox = region.get("bbox")
    if not isinstance(bbox, dict):
        raise HTTPException(status_code=422, detail="perception_ir_snapshot region bbox must be object")

    try:
        x_min = float(bbox["x_min"])
        y_min = float(bbox["y_min"])
        x_max = float(bbox["x_max"])
        y_max = float(bbox["y_max"])
    except Exception:
        raise HTTPException(status_code=422, detail="perception_ir_snapshot bbox malformed")

    return [x_min * image_width, y_min * image_height, x_max * image_width, y_max * image_height]


def _validate_annotation_anchor(payload: AnnotationFeedbackRequest) -> List[float]:
    """
    Enforce strict spatial anchor contract:
    - region_id must exist in PerceptionIR snapshot
    - region_type must match
    - submitted bbox must be fully contained in source region bbox
    """
    bbox_abs = _validate_bbox_in_image_space(
        bbox=payload.bbox,
        image_width=payload.image_width,
        image_height=payload.image_height,
    )

    region = _extract_layout_region_from_snapshot(payload.perception_ir_snapshot, payload.region_id)
    if region is None:
        raise HTTPException(status_code=422, detail="region_id not found in perception_ir_snapshot")

    source_region_type = str(region.get("region_type") or "")
    if source_region_type != payload.region_type:
        raise HTTPException(status_code=422, detail="region_type mismatch against perception_ir_snapshot")

    source_bbox_abs = _bbox_from_snapshot_region(
        region,
        image_width=payload.image_width,
        image_height=payload.image_height,
    )
    sx1, sy1, sx2, sy2 = source_bbox_abs
    x1, y1, x2, y2 = bbox_abs
    if x1 < sx1 or y1 < sy1 or x2 > sx2 or y2 > sy2:
        raise HTTPException(status_code=422, detail="bbox is outside source perception region")

    # Cognition snapshot anchor check: reference this region_id explicitly
    evaluations = payload.cognitive_ir_snapshot.get("step_evaluations")
    if isinstance(evaluations, list):
        has_anchor = any(
            isinstance(item, dict) and str(item.get("reference_element_id")) == payload.region_id
            for item in evaluations
        )
        if not has_anchor:
            raise HTTPException(status_code=422, detail="cognitive_ir_snapshot lacks region_id anchor")
    else:
        raise HTTPException(status_code=422, detail="cognitive_ir_snapshot.step_evaluations missing")

    return bbox_abs


# --- API Endpoints (Phase 28: Celery-decoupled) ---
@router.post("/rubric/generate", response_model=RubricGenerateResponse, status_code=201)
@limiter.limit("5/minute")
async def generate_rubric_job(
    request: Request,
    files: List[UploadFile] = File(...),
    db_path: str = Depends(get_db_path),
):
    """
    双轨上传-轨道1：
    上传标准答案图片/PDF，直接生成并持久化 rubric，返回 rubric_id。
    """
    files_data = []
    for file in files:
        content = await file.read()
        files_data.append((content, file.filename))

    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    workflow = GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
        skill_service=SkillService(db_path=db_path),
    )

    rubric: TeacherRubric = await workflow.generate_rubric_pipeline(files_data)
    rubric_id = str(uuid.uuid4())
    await save_rubric(
        db_path,
        rubric_id=rubric_id,
        question_id=rubric.question_id,
        rubric_json=rubric.model_dump(),
    )
    return RubricGenerateResponse(
        rubric_id=rubric_id,
        question_id=rubric.question_id,
        grading_points_count=len(rubric.grading_points),
    )


@router.get("/rubrics", response_model=List[RubricSummaryItem])
async def get_rubric_list(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_rubrics(db_path, limit=limit, offset=offset)
    return [RubricSummaryItem(**r) for r in rows]


@router.get("/rubrics/{rubric_id}", response_model=RubricDetailResponse)
async def get_rubric_detail(
    rubric_id: str,
    db_path: str = Depends(get_db_path),
):
    row = await get_rubric(db_path, rubric_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rubric not found")
    rubric_raw = row.get("rubric_json")
    try:
        rubric_obj = json.loads(rubric_raw) if isinstance(rubric_raw, str) else rubric_raw
    except Exception:
        rubric_obj = {}
    return RubricDetailResponse(
        rubric_id=row["rubric_id"],
        question_id=row.get("question_id"),
        created_at=row.get("created_at"),
        rubric_json=rubric_obj,
    )


@router.post("/grade/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("100/minute")  # Phase 35: Increased for batch grading (100+ students)
async def submit_grading_job(
    request: Request,
    files: List[UploadFile] = File(...),
    rubric_id: Optional[str] = Form(default=None),
    db_path: str = Depends(get_db_path)
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
    # 1. Generate business task UUID
    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    
    # 2. Store uploaded files via storage adapter (Phase 32)
    file_refs = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)
    
    # 3. Pre-persist task state (PENDING) BEFORE queueing
    await create_task(db_path, task_id)

    bound_rubric: Optional[Dict[str, Any]] = None
    if rubric_id:
        rubric_row = await get_rubric(db_path, rubric_id)
        if not rubric_row:
            raise HTTPException(status_code=404, detail="Rubric not found")
        rubric_raw = rubric_row.get("rubric_json")
        bound_rubric = json.loads(rubric_raw) if isinstance(rubric_raw, str) else rubric_raw
        await set_task_rubric_id(db_path, task_id, rubric_id)
    
    # 4. Dispatch to Celery worker queue (non-blocking)
    # Phase 32: Storage Adapter - enqueue URIs (backend-agnostic)
    payload = storage.prepare_payload(file_refs)
    if bound_rubric is not None:
        payload["rubric_json"] = bound_rubric
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
    return TaskResponse(task_id=task_id, status="PENDING", rubric_id=rubric_id)


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
        "grading_status": task.get("grading_status"),
        "rubric_id": task.get("rubric_id"),
        "review_status": task.get("review_status"),
        "fallback_reason": task.get("fallback_reason"),
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
    
    elif task["status"] == "COMPLETED":
        if task.get("grading_status") == "REJECTED_UNREADABLE":
            response_data["error_code"] = "INPUT_REJECTED"
            response_data["error_message"] = task.get("error_message", "Input quality too low")
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


@router.get("/tasks/pending-review", response_model=List[PendingReviewTaskItem])
async def get_pending_review_tasks(
    status: Optional[str] = Query(default=None, pattern="^(SCORED|REJECTED_UNREADABLE)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    # status query maps to grading_status filter to keep pipeline status and business status separated.
    rows = await list_pending_review_tasks(
        db_path,
        grading_status_filter=status,
        limit=limit,
        offset=offset,
    )
    normalized = []
    for row in rows:
        normalized.append(PendingReviewTaskItem(**row))
    return normalized


@router.get("/hygiene/interceptions", response_model=List[HygieneInterceptionItem])
async def get_hygiene_interceptions(
    interception_node: Optional[str] = Query(default=None, pattern="^(blank|short_circuit|unreadable)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_hygiene_interceptions(
        db_path,
        interception_node_filter=interception_node,
        limit=limit,
        offset=offset,
    )
    return [HygieneInterceptionItem(**r) for r in rows]


@router.post("/hygiene/interceptions/{record_id}/action", response_model=HygieneInterceptionItem)
async def update_hygiene_action(
    record_id: int,
    payload: HygieneActionUpdateRequest,
    db_path: str = Depends(get_db_path),
):
    if payload.action not in {"discard", "manual_review"}:
        raise HTTPException(status_code=422, detail="action must be discard or manual_review")
    updated = await update_hygiene_interception_action(
        db_path,
        record_id=record_id,
        action=payload.action,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Hygiene interception record not found")
    row = await get_hygiene_interception_by_id(db_path, record_id=record_id)
    if not row:
        raise HTTPException(status_code=404, detail="Hygiene interception record not found")
    return HygieneInterceptionItem(**row)


@router.post("/hygiene/interceptions/bulk-action")
async def bulk_update_hygiene_action(
    payload: HygieneBulkActionUpdateRequest,
    db_path: str = Depends(get_db_path),
):
    if payload.action not in {"discard", "manual_review"}:
        raise HTTPException(status_code=422, detail="action must be discard or manual_review")
    if not payload.record_ids:
        raise HTTPException(status_code=422, detail="record_ids must not be empty")
    affected = await bulk_update_hygiene_interception_action(
        db_path,
        record_ids=payload.record_ids,
        action=payload.action,
    )
    return {"updated_count": affected}


@router.post("/annotations/feedback", response_model=AnnotationFeedbackResponse)
async def submit_annotation_feedback(
    payload: AnnotationFeedbackRequest,
    db_path: str = Depends(get_db_path),
):
    task = await get_task(db_path, payload.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("grading_status") != "SCORED":
        raise HTTPException(status_code=422, detail="Only SCORED tasks can produce golden annotation assets")

    bbox_abs = _validate_annotation_anchor(payload)
    trace_id = get_trace_id()
    await create_golden_annotation_asset(
        db_path,
        trace_id=trace_id,
        task_id=payload.task_id,
        region_id=payload.region_id,
        region_type=payload.region_type,
        image_width=payload.image_width,
        image_height=payload.image_height,
        bbox_coordinates=bbox_abs,
        perception_ir_snapshot=payload.perception_ir_snapshot,
        cognitive_ir_snapshot=payload.cognitive_ir_snapshot,
        teacher_text_feedback=payload.teacher_text_feedback,
        expected_score=payload.expected_score,
        is_integrated_to_dataset=payload.is_integrated_to_dataset,
    )
    return AnnotationFeedbackResponse(
        status="ACCEPTED",
        trace_id=trace_id,
        task_id=payload.task_id,
        region_id=payload.region_id,
    )


@router.get("/annotations/assets", response_model=List[GoldenAnnotationAssetItem])
async def get_annotation_assets(
    task_id: Optional[str] = Query(default=None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_golden_annotation_assets(
        db_path,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )
    result: List[GoldenAnnotationAssetItem] = []
    for row in rows:
        raw_bbox = row.get("bbox_coordinates")
        bbox_coordinates: List[float] = []
        if isinstance(raw_bbox, str):
            try:
                parsed = json.loads(raw_bbox)
                if isinstance(parsed, list):
                    bbox_coordinates = [float(v) for v in parsed]
            except Exception:
                bbox_coordinates = []
        row["bbox_coordinates"] = bbox_coordinates
        row["is_integrated_to_dataset"] = bool(row.get("is_integrated_to_dataset", 0))
        result.append(GoldenAnnotationAssetItem(**row))
    return result


@router.get("/review/flow-guide", response_model=ReviewFlowGuideResponse)
async def get_review_flow_guide():
    """
    前端对接辅助文档接口：
    给出复核流程核心端点与状态机枚举，方便 UI 快速接入。
    """
    return ReviewFlowGuideResponse(
        pending_list_endpoint="/api/v1/tasks/pending-review?status=REJECTED_UNREADABLE&page=1&limit=20",
        task_status_enum=["PENDING", "PROCESSING", "COMPLETED", "FAILED"],
        grading_status_enum=["SCORED", "REJECTED_UNREADABLE"],
        notes=[
            "前端上传后应优先走 SSE 接口接收状态变化。",
            "pipeline_status=COMPLETED 且 grading_status=REJECTED_UNREADABLE 时，进入人工待办池。",
            "卫生流请走 /api/v1/hygiene/interceptions；黄金反馈流请走 /api/v1/annotations/feedback。",
            "annotations/feedback 使用 (trace_id, region_id) upsert 覆盖提交，保证并发幂等。",
        ],
    )


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


@router.post("/skills/layout/parse")
async def skill_layout_parse(payload: SkillLayoutParseRequest):
    """
    Internal skill gateway endpoint.
    Used when SKILL_LAYOUT_PARSER_API_URL points to local service.
    """
    import base64

    try:
        image_bytes = base64.b64decode(payload.image_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="invalid image_base64") from exc

    engine = create_perception_engine()
    if not hasattr(engine, "extract_layout"):
        raise HTTPException(status_code=501, detail="perception engine does not support layout extraction")

    layout = await engine.extract_layout(
        image_bytes,
        context_type=payload.context_type,
        target_question_no=payload.target_question_no,
        page_index=payload.page_index,
    )
    return {
        "context_type": layout.context_type,
        "target_question_no": layout.target_question_no,
        "page_index": layout.page_index,
        "regions": [
            {
                "target_id": item.target_id,
                "question_no": item.question_no,
                "region_type": item.region_type,
                "bbox": item.bbox.model_dump(),
            }
            for item in layout.regions
        ],
        "warnings": list(layout.warnings),
    }


@router.post("/skills/validate")
async def skill_validate(payload: SkillValidationRequest):
    """
    Internal validation gateway endpoint.
    Default implementation is contract-only and returns deterministic structure.
    """
    validation_input = ValidationInput(
        task_id=payload.task_id,
        question_id=payload.question_id,
        perception_payload=payload.perception_payload,
        evaluation_payload=payload.evaluation_payload,
        rubric_payload=payload.rubric_payload,
    )
    del validation_input
    return {
        "status": "ok",
        "confidence": 0.0,
        "details": {"mode": "gateway_stub"},
        "warnings": ["No external validator configured, stub response used."],
    }
