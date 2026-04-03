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
    get_annotation_asset_by_id,
    get_task_status_counts,
    get_completion_latencies_seconds,
    get_task_volume_stats,
    get_annotation_dataset_stats,
    get_review_queue_stats,
    get_prompt_cache_level_stats,
    get_runtime_telemetry_model_hits,
    get_runtime_telemetry_fallback_stats,
    upsert_prompt_control_state,
    get_prompt_control_state,
    upsert_prompt_ab_config,
    get_prompt_ab_config,
    append_prompt_ops_audit,
    list_prompt_ops_audit,
    get_ops_feature_flags,
    upsert_ops_feature_flags,
)
from src.worker.main import grade_homework_task
from src.core.storage_adapter import storage
from src.core.trace_context import get_trace_id
from src.worker.main import emit_trace_probe
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.schemas.rubric_ir import TeacherRubric
from src.core.config import settings
from src.core.runtime_router import get_runtime_router_controller
from src.skills.service import SkillService
from src.skills.interfaces import ValidationInput
from src.prompts.provider import get_prompt_provider
from src.prompts.schemas import PromptInvalidationEvent
from src.perception.factory import create_perception_engine


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
                    raise HTTPException(
                        status_code=408,
                        detail=_error_detail(
                            error_code="UPLOAD_TIMEOUT",
                            message="upload read timeout",
                            retryable=True,
                            retry_hint="retry_submit",
                            next_action="retry_upload",
                        ),
                    ) from exc
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > settings.max_request_body_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=_error_detail(
                            error_code="FILE_TOO_LARGE",
                            message="file too large",
                            retryable=False,
                            retry_hint="compress_or_split_file",
                            next_action="adjust_file",
                        ),
                    )
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
    status_endpoint: Optional[str] = None
    stream_endpoint: Optional[str] = None
    suggested_poll_interval_seconds: Optional[int] = None

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
    retryable: Optional[bool] = None
    retry_hint: Optional[str] = None
    next_action: Optional[str] = None
    status_endpoint: Optional[str] = None
    stream_endpoint: Optional[str] = None
    suggested_poll_interval_seconds: Optional[int] = None
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


class PendingReviewTasksResponse(BaseModel):
    page: int
    limit: int
    items: List[PendingReviewTaskItem]


class ReviewFlowGuideResponse(BaseModel):
    pending_list_endpoint: str
    task_status_enum: List[str]
    grading_status_enum: List[str]
    notes: List[str]


class GradeFlowGuideResponse(BaseModel):
    submit_endpoint: str
    status_endpoint_template: str
    stream_endpoint_template: str
    task_status_enum: List[str]
    terminal_statuses: List[str]
    error_code_actions: Dict[str, str]
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


class AnnotationAssetDetailResponse(BaseModel):
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
    perception_ir_snapshot: Dict[str, Any]
    cognitive_ir_snapshot: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AnnotationAssetListResponse(BaseModel):
    page: int
    limit: int
    items: List[GoldenAnnotationAssetItem]


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


class CapabilityEndpointItem(BaseModel):
    method: str
    path: str
    response_model: Optional[str] = None
    notes: Optional[str] = None


class CapabilityDomainItem(BaseModel):
    domain: str
    endpoints: List[CapabilityEndpointItem]


class CapabilityCatalogResponse(BaseModel):
    version: str
    domains: List[CapabilityDomainItem]


class ContractFieldItem(BaseModel):
    name: str
    type: str
    required: bool


class ContractSchemaItem(BaseModel):
    schema_name: str
    fields: List[ContractFieldItem]


class ApiContractCatalogResponse(BaseModel):
    version: str
    status_enums: Dict[str, List[str]]
    error_codes: List[str]
    schemas: List[ContractSchemaItem]


class SlaSummaryResponse(BaseModel):
    version: str
    queue_latency_target_ms: int
    completion_target_seconds_p95: int
    sse_reliability_target: str
    observed_status_counts: Dict[str, int]
    observed_completion_seconds_p50: Optional[float] = None
    observed_completion_seconds_p95: Optional[float] = None
    notes: List[str] = Field(default_factory=list)


class ProviderBenchmarkResponse(BaseModel):
    version: str
    window_hours: int
    task_volume: Dict[str, int]
    throughput_tasks_per_hour: float
    cognitive_router: Dict[str, Any]
    estimated_cost: Dict[str, float]
    notes: List[str] = Field(default_factory=list)


class RouterPolicyResponse(BaseModel):
    version: str
    policy: Dict[str, Any]
    live_snapshot: Dict[str, Any]
    notes: List[str] = Field(default_factory=list)


class DatasetPipelineSummaryResponse(BaseModel):
    version: str
    window_hours: int
    dataset_assets: Dict[str, int]
    review_queue: Dict[str, int]
    notes: List[str] = Field(default_factory=list)


class RuntimeDashboardResponse(BaseModel):
    version: str
    window_hours: int
    provider_hits: Dict[str, int]
    fallback_triggers: Dict[str, Any]
    prompt_cache_hits: Dict[str, int]
    human_review_rate: float
    notes: List[str] = Field(default_factory=list)


class PromptControlRequest(BaseModel):
    prompt_key: str
    forced_variant_id: Optional[str] = None
    lkg_mode: bool = False
    operator_id: Optional[str] = None


class PromptAbConfigRequest(BaseModel):
    prompt_key: str
    enabled: bool
    rollout_percentage: int = Field(..., ge=0, le=100)
    variant_weights: Dict[str, int] = Field(default_factory=dict)
    segment_prefixes: List[str] = Field(default_factory=list)
    sticky_salt: str = ""
    operator_id: Optional[str] = None


class PromptOpsAuditItem(BaseModel):
    id: int
    trace_id: str
    operator_id: Optional[str] = None
    action: str
    prompt_key: Optional[str] = None
    payload_json: str
    created_at: Optional[str] = None


class OpsProviderSwitchRequest(BaseModel):
    provider: str
    operator_id: Optional[str] = None


class OpsRouterControlRequest(BaseModel):
    enabled: bool
    failure_rate_threshold: float = Field(..., gt=0.0)
    token_spike_threshold: float = Field(..., gt=0.0)
    min_samples: int = Field(..., ge=1)
    budget_token_limit: int = Field(..., ge=1)
    operator_id: Optional[str] = None


class OpsRouterControlResponse(BaseModel):
    enabled: bool
    failure_rate_threshold: float
    token_spike_threshold: float
    min_samples: int
    budget_token_limit: int


class OpsConfigSnapshotResponse(BaseModel):
    perception_provider: str
    prompt_settings: Dict[str, Any]
    router_policy: OpsRouterControlResponse
    environment: str = "dev"
    feature_flags: Dict[str, bool] = Field(default_factory=dict)


class OpsAuditLogItem(BaseModel):
    trace_id: str
    operator_id: Optional[str] = None
    action: str
    component: str
    payload: Dict[str, Any]


class OpsAuditLogResponse(BaseModel):
    page: int
    limit: int
    items: List[OpsAuditLogItem]


class OpsPromptCatalogItem(BaseModel):
    prompt_key: str
    control_state: Dict[str, Any]
    ab_state: Dict[str, Any]
    runtime_state: Dict[str, Any]


class OpsPromptCatalogResponse(BaseModel):
    page: int
    limit: int
    items: List[OpsPromptCatalogItem]


class OpsFeatureFlagsRequest(BaseModel):
    deployment_environment: Literal["dev", "staging", "prod"]
    provider_switch_enabled: bool = True
    prompt_control_enabled: bool = True
    router_control_enabled: bool = True
    operator_id: Optional[str] = None


class OpsFeatureFlagsResponse(BaseModel):
    deployment_environment: Literal["dev", "staging", "prod"]
    provider_switch_enabled: bool
    prompt_control_enabled: bool
    router_control_enabled: bool
    updated_at: Optional[str] = None


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    sorted_values = sorted(values)
    idx = int(round((len(sorted_values) - 1) * p))
    idx = max(0, min(idx, len(sorted_values) - 1))
    return float(sorted_values[idx])


def _schema_fields_from_model(model: type[BaseModel]) -> List[ContractFieldItem]:
    fields: List[ContractFieldItem] = []
    for name, field in model.model_fields.items():
        required = field.is_required()
        annotation = field.annotation
        fields.append(
            ContractFieldItem(
                name=name,
                type=str(annotation),
                required=required,
            )
        )
    return fields


def _error_detail(
    *,
    error_code: str,
    message: str,
    retryable: bool = False,
    retry_hint: Optional[str] = None,
    next_action: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
    }
    if retry_hint:
        payload["retry_hint"] = retry_hint
    if next_action:
        payload["next_action"] = next_action
    return payload


def _load_settings_from_env() -> None:
    settings.__dict__.clear()
    refreshed = settings.__class__()  # type: ignore[call-arg]
    settings.__dict__.update(refreshed.__dict__)


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
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="RUBRIC_NOT_FOUND",
                message="Rubric not found",
                retryable=False,
                next_action="select_valid_rubric",
            ),
        )
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
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=rubric_id,
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
        status_endpoint_template="/api/v1/grade/{task_id}",
        stream_endpoint_template="/api/v1/tasks/{task_id}/stream",
        task_status_enum=["PENDING", "PROCESSING", "COMPLETED", "FAILED"],
        terminal_statuses=["COMPLETED", "FAILED"],
        error_code_actions={
            "UPLOAD_TIMEOUT": "retry_upload",
            "FILE_TOO_LARGE": "adjust_file",
            "TASK_NOT_FOUND": "submit_new_task",
            "TASK_NOT_COMPLETED": "wait_for_completion",
            "INPUT_REJECTED": "retry_upload",
            "TASK_FAILED": "retry_upload",
            "INTERNAL_ERROR": "contact_support",
            "SSE_BACKEND_UNAVAILABLE": "fallback_to_polling",
        },
        notes=[
            "优先使用 SSE，若出现 SSE_BACKEND_UNAVAILABLE 则回退到状态轮询。",
            "状态轮询建议间隔 2 秒；可配合 ETag 条件请求降低带宽。",
            "FAILED 与 REJECTED_UNREADABLE 默认允许重新提交。",
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
        "fallback_reason": task.get("fallback_reason"),
        "retryable": False,
        "status_endpoint": f"/api/v1/grade/{task_id}",
        "stream_endpoint": f"/api/v1/tasks/{task_id}/stream",
        "suggested_poll_interval_seconds": 2,
    }
    
    # Phase 29: Status-specific enrichment
    if task["status"] in ["PENDING", "PROCESSING"]:
        # TODO: Implement progress tracking via Redis/Celery backend
        response_data["progress"] = 0.5 if task["status"] == "PROCESSING" else 0.0
        response_data["eta_seconds"] = 45  # Placeholder: avg grading time
        response_data["next_action"] = "wait_for_completion"
    
    elif task["status"] == "FAILED":
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
        if task.get("grading_status") == "REJECTED_UNREADABLE":
            response_data["error_code"] = "INPUT_REJECTED"
            response_data["error_message"] = task.get("error_message", "Input quality too low")
            response_data["retryable"] = True
            response_data["retry_hint"] = "resubmit_with_clearer_image"
            response_data["next_action"] = "retry_upload"
        else:
            response_data["next_action"] = "view_results"
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


@router.get("/tasks/pending-review", response_model=List[PendingReviewTaskItem])
async def get_pending_review_tasks(
    status: Optional[str] = Query(default=None, pattern="^(SCORED|REJECTED_UNREADABLE)$"),
    task_id: Optional[str] = Query(default=None),
    sort_by: str = Query(default="updated_at", pattern="^(updated_at|created_at|task_id)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    # status query maps to grading_status filter to keep pipeline status and business status separated.
    rows = await list_pending_review_tasks(
        db_path,
        task_id=task_id,
        grading_status_filter=status,
        order_by=sort_by,
        order_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    normalized = []
    for row in rows:
        normalized.append(PendingReviewTaskItem(**row))
    return normalized


@router.get("/review/pending-workbench", response_model=PendingReviewTasksResponse)
async def get_pending_review_workbench(
    status: Optional[str] = Query(default=None, pattern="^(SCORED|REJECTED_UNREADABLE)$"),
    task_id: Optional[str] = Query(default=None),
    sort_by: str = Query(default="updated_at", pattern="^(updated_at|created_at|task_id)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_pending_review_tasks(
        db_path,
        task_id=task_id,
        grading_status_filter=status,
        order_by=sort_by,
        order_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    return PendingReviewTasksResponse(
        page=page,
        limit=limit,
        items=[PendingReviewTaskItem(**row) for row in rows],
    )


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
    region_id: Optional[str] = Query(default=None),
    region_type: Optional[str] = Query(default=None, pattern="^(question_region|answer_region)$"),
    integrated_only: Optional[bool] = Query(default=None),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|id)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_golden_annotation_assets(
        db_path,
        task_id=task_id,
        region_id=region_id,
        region_type=region_type,
        integrated_only=integrated_only,
        order_by=sort_by,
        order_direction=sort_direction,
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


@router.get("/review/annotation-assets", response_model=AnnotationAssetListResponse)
async def get_review_annotation_assets(
    task_id: Optional[str] = Query(default=None),
    region_id: Optional[str] = Query(default=None),
    region_type: Optional[str] = Query(default=None, pattern="^(question_region|answer_region)$"),
    integrated_only: Optional[bool] = Query(default=None),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at|id)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_golden_annotation_assets(
        db_path,
        task_id=task_id,
        region_id=region_id,
        region_type=region_type,
        integrated_only=integrated_only,
        order_by=sort_by,
        order_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    items: List[GoldenAnnotationAssetItem] = []
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
        items.append(GoldenAnnotationAssetItem(**row))
    return AnnotationAssetListResponse(page=page, limit=limit, items=items)


@router.get("/review/annotation-assets/{asset_id}", response_model=AnnotationAssetDetailResponse)
async def get_review_annotation_asset_detail(
    asset_id: int,
    db_path: str = Depends(get_db_path),
):
    row = await get_annotation_asset_by_id(db_path, asset_id=asset_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="ANNOTATION_ASSET_NOT_FOUND",
                message="Annotation asset not found",
                retryable=False,
                next_action="refresh_asset_list",
            ),
        )

    raw_bbox = row.get("bbox_coordinates")
    bbox_coordinates: List[float] = []
    if isinstance(raw_bbox, str):
        try:
            parsed = json.loads(raw_bbox)
            if isinstance(parsed, list):
                bbox_coordinates = [float(v) for v in parsed]
        except Exception:
            bbox_coordinates = []

    raw_perception = row.get("perception_ir_snapshot")
    raw_cognitive = row.get("cognitive_ir_snapshot")
    try:
        perception_snapshot = json.loads(raw_perception) if isinstance(raw_perception, str) else raw_perception
    except Exception:
        perception_snapshot = {}
    try:
        cognitive_snapshot = json.loads(raw_cognitive) if isinstance(raw_cognitive, str) else raw_cognitive
    except Exception:
        cognitive_snapshot = {}

    return AnnotationAssetDetailResponse(
        id=int(row["id"]),
        trace_id=str(row["trace_id"]),
        task_id=str(row["task_id"]),
        region_id=str(row["region_id"]),
        region_type=str(row["region_type"]),
        image_width=int(row["image_width"]),
        image_height=int(row["image_height"]),
        bbox_coordinates=bbox_coordinates,
        teacher_text_feedback=str(row["teacher_text_feedback"]),
        expected_score=float(row["expected_score"]),
        is_integrated_to_dataset=bool(row.get("is_integrated_to_dataset", 0)),
        perception_ir_snapshot=perception_snapshot if isinstance(perception_snapshot, dict) else {},
        cognitive_ir_snapshot=cognitive_snapshot if isinstance(cognitive_snapshot, dict) else {},
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


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


@router.get("/capabilities/catalog", response_model=CapabilityCatalogResponse)
async def get_capability_catalog():
    return CapabilityCatalogResponse(
        version="1.0",
        domains=[
            CapabilityDomainItem(
                domain="rubric",
                endpoints=[
                    CapabilityEndpointItem(method="POST", path="/api/v1/rubric/generate", response_model="RubricGenerateResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/rubrics", response_model="List[RubricSummaryItem]"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/rubrics/{rubric_id}", response_model="RubricDetailResponse"),
                ],
            ),
            CapabilityDomainItem(
                domain="grade",
                endpoints=[
                    CapabilityEndpointItem(method="POST", path="/api/v1/grade/submit", response_model="TaskResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/grade/{task_id}", response_model="TaskStatusResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/tasks/{task_id}/stream", notes="SSE stream"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/grade/flow-guide", response_model="GradeFlowGuideResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/results", response_model="List[GradingResultItem]"),
                ],
            ),
            CapabilityDomainItem(
                domain="review",
                endpoints=[
                    CapabilityEndpointItem(method="GET", path="/api/v1/tasks/pending-review", response_model="List[PendingReviewTaskItem]"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/review/pending-workbench", response_model="PendingReviewTasksResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/review/annotation-assets", response_model="AnnotationAssetListResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/review/annotation-assets/{asset_id}", response_model="AnnotationAssetDetailResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/review/flow-guide", response_model="ReviewFlowGuideResponse"),
                ],
            ),
            CapabilityDomainItem(
                domain="annotation",
                endpoints=[
                    CapabilityEndpointItem(method="POST", path="/api/v1/annotations/feedback", response_model="AnnotationFeedbackResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/annotations/assets", response_model="List[GoldenAnnotationAssetItem]"),
                ],
            ),
            CapabilityDomainItem(
                domain="hygiene",
                endpoints=[
                    CapabilityEndpointItem(method="GET", path="/api/v1/hygiene/interceptions", response_model="List[HygieneInterceptionItem]"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/hygiene/interceptions/{record_id}/action", response_model="HygieneInterceptionItem"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/hygiene/interceptions/bulk-action"),
                ],
            ),
            CapabilityDomainItem(
                domain="obs",
                endpoints=[
                    CapabilityEndpointItem(method="POST", path="/api/v1/trace/probe", response_model="TraceProbeResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/sla/summary", response_model="SlaSummaryResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/contracts/catalog", response_model="ApiContractCatalogResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/metrics/provider-benchmark", response_model="ProviderBenchmarkResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/router/policy", response_model="RouterPolicyResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/metrics/dataset-pipeline", response_model="DatasetPipelineSummaryResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/metrics/runtime-dashboard", response_model="RuntimeDashboardResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/control"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/ab-config"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/refresh"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/invalidate"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/prompt/state"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/prompt/audit", response_model="List[PromptOpsAuditItem]"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/config/snapshot", response_model="OpsConfigSnapshotResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/feature-flags", response_model="OpsFeatureFlagsResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/feature-flags", response_model="OpsFeatureFlagsResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/provider/switch"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/router/control", response_model="OpsRouterControlResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/prompt/catalog", response_model="OpsPromptCatalogResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/audit/logs", response_model="OpsAuditLogResponse"),
                ],
            ),
        ],
    )


@router.get("/contracts/catalog", response_model=ApiContractCatalogResponse)
async def get_contract_catalog():
    schemas = [
        ContractSchemaItem(schema_name="TaskResponse", fields=_schema_fields_from_model(TaskResponse)),
        ContractSchemaItem(schema_name="TaskStatusResponse", fields=_schema_fields_from_model(TaskStatusResponse)),
        ContractSchemaItem(schema_name="GradeFlowGuideResponse", fields=_schema_fields_from_model(GradeFlowGuideResponse)),
        ContractSchemaItem(schema_name="RubricGenerateResponse", fields=_schema_fields_from_model(RubricGenerateResponse)),
        ContractSchemaItem(schema_name="RubricDetailResponse", fields=_schema_fields_from_model(RubricDetailResponse)),
        ContractSchemaItem(schema_name="PendingReviewTaskItem", fields=_schema_fields_from_model(PendingReviewTaskItem)),
        ContractSchemaItem(schema_name="PendingReviewTasksResponse", fields=_schema_fields_from_model(PendingReviewTasksResponse)),
        ContractSchemaItem(schema_name="AnnotationAssetListResponse", fields=_schema_fields_from_model(AnnotationAssetListResponse)),
        ContractSchemaItem(schema_name="AnnotationAssetDetailResponse", fields=_schema_fields_from_model(AnnotationAssetDetailResponse)),
        ContractSchemaItem(schema_name="AnnotationFeedbackRequest", fields=_schema_fields_from_model(AnnotationFeedbackRequest)),
        ContractSchemaItem(schema_name="AnnotationFeedbackResponse", fields=_schema_fields_from_model(AnnotationFeedbackResponse)),
        ContractSchemaItem(schema_name="ProviderBenchmarkResponse", fields=_schema_fields_from_model(ProviderBenchmarkResponse)),
        ContractSchemaItem(schema_name="RouterPolicyResponse", fields=_schema_fields_from_model(RouterPolicyResponse)),
        ContractSchemaItem(schema_name="DatasetPipelineSummaryResponse", fields=_schema_fields_from_model(DatasetPipelineSummaryResponse)),
        ContractSchemaItem(schema_name="RuntimeDashboardResponse", fields=_schema_fields_from_model(RuntimeDashboardResponse)),
        ContractSchemaItem(schema_name="PromptControlRequest", fields=_schema_fields_from_model(PromptControlRequest)),
        ContractSchemaItem(schema_name="PromptAbConfigRequest", fields=_schema_fields_from_model(PromptAbConfigRequest)),
        ContractSchemaItem(schema_name="PromptOpsAuditItem", fields=_schema_fields_from_model(PromptOpsAuditItem)),
        ContractSchemaItem(schema_name="OpsProviderSwitchRequest", fields=_schema_fields_from_model(OpsProviderSwitchRequest)),
        ContractSchemaItem(schema_name="OpsRouterControlRequest", fields=_schema_fields_from_model(OpsRouterControlRequest)),
        ContractSchemaItem(schema_name="OpsRouterControlResponse", fields=_schema_fields_from_model(OpsRouterControlResponse)),
        ContractSchemaItem(schema_name="OpsFeatureFlagsRequest", fields=_schema_fields_from_model(OpsFeatureFlagsRequest)),
        ContractSchemaItem(schema_name="OpsFeatureFlagsResponse", fields=_schema_fields_from_model(OpsFeatureFlagsResponse)),
        ContractSchemaItem(schema_name="OpsConfigSnapshotResponse", fields=_schema_fields_from_model(OpsConfigSnapshotResponse)),
        ContractSchemaItem(schema_name="OpsAuditLogItem", fields=_schema_fields_from_model(OpsAuditLogItem)),
        ContractSchemaItem(schema_name="OpsAuditLogResponse", fields=_schema_fields_from_model(OpsAuditLogResponse)),
        ContractSchemaItem(schema_name="OpsPromptCatalogItem", fields=_schema_fields_from_model(OpsPromptCatalogItem)),
        ContractSchemaItem(schema_name="OpsPromptCatalogResponse", fields=_schema_fields_from_model(OpsPromptCatalogResponse)),
    ]
    return ApiContractCatalogResponse(
        version="1.0",
        status_enums={
            "task_status": ["PENDING", "PROCESSING", "COMPLETED", "FAILED"],
            "grading_status": ["SCORED", "REJECTED_UNREADABLE"],
            "review_status": ["NOT_REQUIRED", "PENDING_REVIEW", "REVIEWED"],
        },
        error_codes=[
            "TASK_NOT_FOUND",
            "TASK_NOT_COMPLETED",
            "RUBRIC_NOT_FOUND",
            "INPUT_REJECTED",
            "TASK_FAILED",
            "INTERNAL_ERROR",
            "SSE_BACKEND_UNAVAILABLE",
            "UPLOAD_TIMEOUT",
            "FILE_TOO_LARGE",
            "ANNOTATION_ASSET_NOT_FOUND",
            "INVALID_PROVIDER",
            "FEATURE_DISABLED",
        ],
        schemas=schemas,
    )


@router.get("/sla/summary", response_model=SlaSummaryResponse)
async def get_sla_summary(
    db_path: str = Depends(get_db_path),
):
    status_counts = await get_task_status_counts(db_path)
    completion_latencies = await get_completion_latencies_seconds(db_path, lookback_hours=24)
    return SlaSummaryResponse(
        version="1.0",
        queue_latency_target_ms=200,
        completion_target_seconds_p95=120,
        sse_reliability_target=">=99.0%",
        observed_status_counts=status_counts,
        observed_completion_seconds_p50=_percentile(completion_latencies, 0.50),
        observed_completion_seconds_p95=_percentile(completion_latencies, 0.95),
        notes=[
            "queue latency target measures submit->worker-processing transition.",
            "completion latency uses task.created_at to first grading_results.created_at.",
            "SSE reliability should be monitored with disconnect/error event ratio dashboard.",
        ],
    )


@router.get("/metrics/provider-benchmark", response_model=ProviderBenchmarkResponse)
async def get_provider_benchmark(
    db_path: str = Depends(get_db_path),
    window_hours: int = Query(24, ge=1, le=168),
):
    volume = await get_task_volume_stats(db_path, lookback_hours=window_hours)
    router_snapshot = get_runtime_router_controller().snapshot()
    completed = int(volume.get("completed_count", 0))
    failed = int(volume.get("failed_count", 0))
    total_count = int(volume.get("total_count", 0))
    fallback_rate = float(router_snapshot.get("fallback_rate", 0.0))
    # Cost proxy (placeholder): relative unit cost where reasoner=1.0 and chat=0.35.
    estimated_reasoner_units = max(completed - int(completed * fallback_rate), 0)
    estimated_chat_units = int(completed * fallback_rate)
    estimated_cost_units = float(estimated_reasoner_units) * 1.0 + float(estimated_chat_units) * 0.35
    failure_rate = float(router_snapshot.get("failure_rate", 0.0))
    success_rate = 1.0 - failure_rate if failure_rate <= 1.0 else 0.0
    throughput = float(total_count) / float(window_hours)
    return ProviderBenchmarkResponse(
        version="1.0",
        window_hours=window_hours,
        task_volume=volume,
        throughput_tasks_per_hour=throughput,
        cognitive_router={
            "requested_model": settings.deepseek_model_name,
            "fallback_model": "deepseek-chat",
            "sample_count": int(router_snapshot.get("sample_count", 0)),
            "failure_rate": failure_rate,
            "fallback_rate": fallback_rate,
            "accuracy_proxy": success_rate,
            "token_median": float(router_snapshot.get("token_median", 0.0)),
            "token_p95": float(router_snapshot.get("token_p95", 0.0)),
        },
        estimated_cost={
            "reasoner_units": float(estimated_reasoner_units),
            "chat_units": float(estimated_chat_units),
            "total_units": estimated_cost_units,
        },
        notes=[
            "Cost is an internal proxy unit and not a billing invoice.",
            "Fallback rate comes from runtime router event stream in process memory.",
        ],
    )


@router.get("/router/policy", response_model=RouterPolicyResponse)
async def get_router_policy():
    live = get_runtime_router_controller().snapshot()
    return RouterPolicyResponse(
        version="1.0",
        policy={
            "auto_controller_enabled": settings.auto_circuit_controller_enabled,
            "failure_rate_threshold": settings.auto_circuit_failure_rate_threshold,
            "token_spike_threshold": settings.auto_circuit_token_spike_threshold,
            "min_samples": settings.auto_circuit_min_samples,
            "budget_token_limit": settings.router_budget_token_limit,
            "default_model": settings.deepseek_model_name,
            "fallback_model": "deepseek-chat",
        },
        live_snapshot=live,
        notes=[
            "When thresholds are exceeded, cognitive route is forced to fallback model.",
            "Token spike compares incoming estimate against rolling median.",
        ],
    )


@router.get("/metrics/dataset-pipeline", response_model=DatasetPipelineSummaryResponse)
async def get_dataset_pipeline_summary(
    db_path: str = Depends(get_db_path),
    window_hours: int = Query(24, ge=1, le=168),
):
    dataset_assets = await get_annotation_dataset_stats(db_path, lookback_hours=window_hours)
    review_queue = await get_review_queue_stats(db_path, lookback_hours=window_hours)
    return DatasetPipelineSummaryResponse(
        version="1.0",
        window_hours=window_hours,
        dataset_assets=dataset_assets,
        review_queue=review_queue,
        notes=[
            "dataset_assets reflects golden annotation ingestion closure.",
            "review_queue reflects manual-review backlog and processed volume.",
        ],
    )


@router.get("/metrics/runtime-dashboard", response_model=RuntimeDashboardResponse)
async def get_runtime_dashboard(
    db_path: str = Depends(get_db_path),
    window_hours: int = Query(24, ge=1, le=168),
):
    review_queue = await get_review_queue_stats(db_path, lookback_hours=window_hours)
    volume = await get_task_volume_stats(db_path, lookback_hours=window_hours)
    prompt_cache = await get_prompt_cache_level_stats(db_path, lookback_hours=window_hours)
    provider_hits = await get_runtime_telemetry_model_hits(db_path, lookback_hours=window_hours)
    fallback_stats = await get_runtime_telemetry_fallback_stats(db_path, lookback_hours=window_hours)

    pending_review = int(review_queue.get("pending_review_count", 0))
    reviewed = int(review_queue.get("reviewed_count", 0))
    review_base = pending_review + reviewed
    human_review_rate = (float(pending_review) / float(review_base)) if review_base > 0 else 0.0

    reason_hits = fallback_stats.get("reason_hits")
    if not isinstance(reason_hits, dict):
        reason_hits = {}

    fallback_triggers = {
        "fallback_rate": float(fallback_stats.get("fallback_rate", 0.0)),
        "fallback_trigger_count": int(fallback_stats.get("fallback_count", 0)),
        "network_error": int(reason_hits.get("network_error", 0)),
        "api_error": int(reason_hits.get("api_error", 0)),
        "parse_error": int(reason_hits.get("parse_error", 0)),
        "rate_limit": int(reason_hits.get("rate_limit", 0)),
        "failure_rate_threshold": int(reason_hits.get("failure_rate_threshold", 0)),
        "token_spike_threshold": int(reason_hits.get("token_spike_threshold", 0)),
        "budget_token_limit": int(reason_hits.get("budget_token_limit", 0)),
        "readability_heavily_altered": int(reason_hits.get("readability_heavily_altered", 0)),
    }
    return RuntimeDashboardResponse(
        version="1.0",
        window_hours=window_hours,
        provider_hits={str(k): int(v) for k, v in provider_hits.items()} if isinstance(provider_hits, dict) else {},
        fallback_triggers=fallback_triggers,
        prompt_cache_hits=prompt_cache,
        human_review_rate=human_review_rate,
        notes=[
            "runtime dashboard now reads durable telemetry from DB.",
            f"task_volume_total={int(volume.get('total_count', 0))}",
        ],
    )


@router.post("/prompt/control")
async def set_prompt_control(
    payload: PromptControlRequest,
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    if not bool(flags.get("prompt_control_enabled", settings.feature_flag_prompt_control)):
        raise HTTPException(
            status_code=403,
            detail=_error_detail(
                error_code="FEATURE_DISABLED",
                message="prompt control is disabled by feature flag",
                retryable=False,
                next_action="contact_ops_admin",
            ),
        )
    provider = get_prompt_provider()
    provider.set_forced_variant(prompt_key=payload.prompt_key, variant_id=payload.forced_variant_id)
    provider.set_lkg_mode(prompt_key=payload.prompt_key, enabled=payload.lkg_mode)

    await upsert_prompt_control_state(
        db_path,
        prompt_key=payload.prompt_key,
        forced_variant_id=payload.forced_variant_id,
        lkg_mode=payload.lkg_mode,
    )
    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=payload.operator_id,
        action="prompt_control_set",
        prompt_key=payload.prompt_key,
        payload_json=payload.model_dump(),
    )
    return {"status": "ok", "prompt_key": payload.prompt_key}


@router.post("/prompt/ab-config")
async def set_prompt_ab_config(
    payload: PromptAbConfigRequest,
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    if not bool(flags.get("prompt_control_enabled", settings.feature_flag_prompt_control)):
        raise HTTPException(
            status_code=403,
            detail=_error_detail(
                error_code="FEATURE_DISABLED",
                message="prompt control is disabled by feature flag",
                retryable=False,
                next_action="contact_ops_admin",
            ),
        )
    provider = get_prompt_provider()
    provider.set_ab_config(
        prompt_key=payload.prompt_key,
        enabled=payload.enabled,
        rollout_percentage=payload.rollout_percentage,
        variant_weights=payload.variant_weights,
        segment_prefixes=payload.segment_prefixes,
        sticky_salt=payload.sticky_salt,
    )
    await upsert_prompt_ab_config(
        db_path,
        prompt_key=payload.prompt_key,
        enabled=payload.enabled,
        rollout_percentage=payload.rollout_percentage,
        variant_weights=payload.variant_weights,
        segment_prefixes=payload.segment_prefixes,
        sticky_salt=payload.sticky_salt,
    )
    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=payload.operator_id,
        action="prompt_ab_config_set",
        prompt_key=payload.prompt_key,
        payload_json=payload.model_dump(),
    )
    return {"status": "ok", "prompt_key": payload.prompt_key}


@router.post("/prompt/refresh")
async def refresh_prompt_assets(
    prompt_key: Optional[str] = Query(default=None),
    operator_id: Optional[str] = Query(default=None),
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    if not bool(flags.get("prompt_control_enabled", settings.feature_flag_prompt_control)):
        raise HTTPException(
            status_code=403,
            detail=_error_detail(
                error_code="FEATURE_DISABLED",
                message="prompt control is disabled by feature flag",
                retryable=False,
                next_action="contact_ops_admin",
            ),
        )
    provider = get_prompt_provider()
    report = await provider.refresh(prompt_key=prompt_key)
    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=operator_id,
        action="prompt_refresh",
        prompt_key=prompt_key,
        payload_json={
            "checked_assets": report.checked_assets,
            "refreshed_assets": report.refreshed_assets,
            "invalidated_assets": report.invalidated_assets,
        },
    )
    return {
        "status": "ok",
        "checked_assets": report.checked_assets,
        "refreshed_assets": report.refreshed_assets,
        "invalidated_assets": report.invalidated_assets,
    }


@router.post("/prompt/invalidate")
async def invalidate_prompt_asset(
    prompt_key: str = Query(...),
    operator_id: Optional[str] = Query(default=None),
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    if not bool(flags.get("prompt_control_enabled", settings.feature_flag_prompt_control)):
        raise HTTPException(
            status_code=403,
            detail=_error_detail(
                error_code="FEATURE_DISABLED",
                message="prompt control is disabled by feature flag",
                retryable=False,
                next_action="contact_ops_admin",
            ),
        )
    provider = get_prompt_provider()
    try:
        await provider.invalidate(
            PromptInvalidationEvent(
                prompt_key=prompt_key,
                version_hash="manual",
                source="manual-api",
            )
        )
    except Exception as exc:
        logger.warning("prompt_invalidate_failed", extra={"extra_fields": {"prompt_key": prompt_key, "error": str(exc)}})
        raise HTTPException(status_code=503, detail="prompt invalidate unavailable") from exc
    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=operator_id,
        action="prompt_invalidate",
        prompt_key=prompt_key,
        payload_json={"prompt_key": prompt_key},
    )
    return {"status": "ok", "prompt_key": prompt_key}


@router.get("/prompt/state")
async def get_prompt_state(
    prompt_key: str = Query(...),
    db_path: str = Depends(get_db_path),
):
    provider = get_prompt_provider()
    control_state = await get_prompt_control_state(db_path, prompt_key=prompt_key)
    ab_state = await get_prompt_ab_config(db_path, prompt_key=prompt_key)
    return {
        "prompt_key": prompt_key,
        "runtime": {
            "forced_variant_id": provider.get_forced_variant(prompt_key=prompt_key),
            "lkg_mode": provider.get_lkg_mode(prompt_key=prompt_key),
            "ab_config": provider.get_ab_config(prompt_key=prompt_key),
        },
        "persisted": {
            "control_state": control_state,
            "ab_state": ab_state,
        },
    }


@router.get("/prompt/audit", response_model=List[PromptOpsAuditItem])
async def get_prompt_ops_audit(
    prompt_key: Optional[str] = Query(default=None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_prompt_ops_audit(
        db_path,
        prompt_key=prompt_key,
        limit=limit,
        offset=offset,
    )
    return [PromptOpsAuditItem(**r) for r in rows]


@router.get("/ops/config/snapshot", response_model=OpsConfigSnapshotResponse)
async def get_ops_config_snapshot(
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    return OpsConfigSnapshotResponse(
        perception_provider=str(settings.perception_provider),
        prompt_settings={
            "prompts_dir": settings.prompts_dir,
            "pull_interval_seconds": settings.prompt_pull_interval_seconds,
            "l1_ttl_seconds": settings.prompt_l1_ttl_seconds,
            "l2_ttl_seconds": settings.prompt_l2_ttl_seconds,
            "invalidation_bus_enabled": settings.prompt_invalidation_bus_enabled,
        },
        router_policy=OpsRouterControlResponse(
            enabled=bool(settings.auto_circuit_controller_enabled),
            failure_rate_threshold=float(settings.auto_circuit_failure_rate_threshold),
            token_spike_threshold=float(settings.auto_circuit_token_spike_threshold),
            min_samples=int(settings.auto_circuit_min_samples),
            budget_token_limit=int(settings.router_budget_token_limit),
        ),
        environment=str(flags.get("deployment_environment") or settings.deployment_environment),
        feature_flags={
            "provider_switch_enabled": bool(flags.get("provider_switch_enabled", settings.feature_flag_provider_switch)),
            "prompt_control_enabled": bool(flags.get("prompt_control_enabled", settings.feature_flag_prompt_control)),
            "router_control_enabled": bool(flags.get("router_control_enabled", settings.feature_flag_router_control)),
        },
    )


@router.post("/ops/provider/switch")
async def switch_ops_provider(
    payload: OpsProviderSwitchRequest,
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    if not bool(flags.get("provider_switch_enabled", settings.feature_flag_provider_switch)):
        raise HTTPException(
            status_code=403,
            detail=_error_detail(
                error_code="FEATURE_DISABLED",
                message="provider switch is disabled by feature flag",
                retryable=False,
                next_action="contact_ops_admin",
            ),
        )
    provider = str(payload.provider).strip().lower()
    from src.perception.factory import list_supported_perception_providers

    supported = list_supported_perception_providers()
    if provider not in supported:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INVALID_PROVIDER",
                message=f"Unsupported perception provider: {provider}",
                retryable=False,
                next_action="choose_supported_provider",
            ),
        )

    settings.perception_provider = provider
    _load_settings_from_env()
    settings.perception_provider = provider
    _ = create_perception_engine()

    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=payload.operator_id,
        action="ops_provider_switch",
        prompt_key=None,
        payload_json={"provider": provider},
    )
    return {"status": "ok", "provider": provider}


@router.post("/ops/router/control", response_model=OpsRouterControlResponse)
async def update_ops_router_control(
    payload: OpsRouterControlRequest,
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    if not bool(flags.get("router_control_enabled", settings.feature_flag_router_control)):
        raise HTTPException(
            status_code=403,
            detail=_error_detail(
                error_code="FEATURE_DISABLED",
                message="router control is disabled by feature flag",
                retryable=False,
                next_action="contact_ops_admin",
            ),
        )
    settings.auto_circuit_controller_enabled = bool(payload.enabled)
    settings.auto_circuit_failure_rate_threshold = float(payload.failure_rate_threshold)
    settings.auto_circuit_token_spike_threshold = float(payload.token_spike_threshold)
    settings.auto_circuit_min_samples = int(payload.min_samples)
    settings.router_budget_token_limit = int(payload.budget_token_limit)

    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=payload.operator_id,
        action="ops_router_control_update",
        prompt_key=None,
        payload_json=payload.model_dump(),
    )
    return OpsRouterControlResponse(
        enabled=bool(settings.auto_circuit_controller_enabled),
        failure_rate_threshold=float(settings.auto_circuit_failure_rate_threshold),
        token_spike_threshold=float(settings.auto_circuit_token_spike_threshold),
        min_samples=int(settings.auto_circuit_min_samples),
        budget_token_limit=int(settings.router_budget_token_limit),
    )


@router.get("/ops/feature-flags", response_model=OpsFeatureFlagsResponse)
async def get_ops_feature_flags_endpoint(
    db_path: str = Depends(get_db_path),
):
    flags = await get_ops_feature_flags(db_path)
    return OpsFeatureFlagsResponse(
        deployment_environment=str(flags.get("deployment_environment") or "dev"),  # type: ignore[arg-type]
        provider_switch_enabled=bool(flags.get("provider_switch_enabled", True)),
        prompt_control_enabled=bool(flags.get("prompt_control_enabled", True)),
        router_control_enabled=bool(flags.get("router_control_enabled", True)),
        updated_at=flags.get("updated_at"),
    )


@router.post("/ops/feature-flags", response_model=OpsFeatureFlagsResponse)
async def set_ops_feature_flags(
    payload: OpsFeatureFlagsRequest,
    db_path: str = Depends(get_db_path),
):
    settings.deployment_environment = payload.deployment_environment
    settings.feature_flag_provider_switch = bool(payload.provider_switch_enabled)
    settings.feature_flag_prompt_control = bool(payload.prompt_control_enabled)
    settings.feature_flag_router_control = bool(payload.router_control_enabled)
    await upsert_ops_feature_flags(
        db_path,
        deployment_environment=payload.deployment_environment,
        provider_switch_enabled=payload.provider_switch_enabled,
        prompt_control_enabled=payload.prompt_control_enabled,
        router_control_enabled=payload.router_control_enabled,
    )
    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=payload.operator_id,
        action="ops_feature_flags_update",
        prompt_key=None,
        payload_json=payload.model_dump(),
    )
    flags = await get_ops_feature_flags(db_path)
    return OpsFeatureFlagsResponse(
        deployment_environment=str(flags.get("deployment_environment") or payload.deployment_environment),  # type: ignore[arg-type]
        provider_switch_enabled=bool(flags.get("provider_switch_enabled", payload.provider_switch_enabled)),
        prompt_control_enabled=bool(flags.get("prompt_control_enabled", payload.prompt_control_enabled)),
        router_control_enabled=bool(flags.get("router_control_enabled", payload.router_control_enabled)),
        updated_at=flags.get("updated_at"),
    )


@router.get("/ops/prompt/catalog", response_model=OpsPromptCatalogResponse)
async def get_ops_prompt_catalog(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    provider = get_prompt_provider()
    offset = (page - 1) * limit
    rows = await list_prompt_ops_audit(
        db_path,
        prompt_key=None,
        limit=limit * 10,
        offset=offset,
    )
    prompt_keys: List[str] = []
    seen: set[str] = set()
    for row in rows:
        key = row.get("prompt_key")
        if isinstance(key, str) and key and key not in seen:
            seen.add(key)
            prompt_keys.append(key)
    prompt_keys = prompt_keys[:limit]

    items: List[OpsPromptCatalogItem] = []
    for key in prompt_keys:
        control_state = await get_prompt_control_state(db_path, prompt_key=key)
        ab_state = await get_prompt_ab_config(db_path, prompt_key=key)
        runtime_state = {
            "forced_variant_id": provider.get_forced_variant(prompt_key=key),
            "lkg_mode": provider.get_lkg_mode(prompt_key=key),
            "ab_config": provider.get_ab_config(prompt_key=key),
        }
        items.append(
            OpsPromptCatalogItem(
                prompt_key=key,
                control_state=control_state,
                ab_state=ab_state,
                runtime_state=runtime_state,
            )
        )

    return OpsPromptCatalogResponse(page=page, limit=limit, items=items)


@router.get("/ops/audit/logs", response_model=OpsAuditLogResponse)
async def get_ops_audit_logs(
    prompt_key: Optional[str] = Query(default=None),
    action_prefix: Optional[str] = Query(default=None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_prompt_ops_audit(
        db_path,
        prompt_key=prompt_key,
        limit=limit * 3,
        offset=offset,
    )
    items: List[OpsAuditLogItem] = []
    for row in rows:
        action = str(row.get("action") or "")
        if action_prefix and not action.startswith(action_prefix):
            continue
        payload_raw = row.get("payload_json")
        payload_obj: Dict[str, Any] = {}
        if isinstance(payload_raw, str):
            try:
                parsed = json.loads(payload_raw)
                if isinstance(parsed, dict):
                    payload_obj = parsed
            except Exception:
                payload_obj = {"raw": payload_raw}
        elif isinstance(payload_raw, dict):
            payload_obj = payload_raw
        component = "prompt-control"
        if action.startswith("ops_"):
            component = "ops-control"
        items.append(
            OpsAuditLogItem(
                trace_id=str(row.get("trace_id") or ""),
                operator_id=row.get("operator_id"),
                action=action,
                component=component,
                payload=payload_obj,
            )
        )
        if len(items) >= limit:
            break
    return OpsAuditLogResponse(page=page, limit=limit, items=items)


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
