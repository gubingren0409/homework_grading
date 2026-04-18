import uuid
import json
import logging
import hashlib
import math
import asyncio
import tempfile
import mimetypes
from pathlib import Path
from typing import List, Optional, Dict, Any, Literal
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Request, Response, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from celery.exceptions import OperationalError as CeleryOperationalError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from src.api.dependencies import get_db_path
from src.api.sse import create_sse_response
from src.core.config import settings
from src.db.client import (
    create_task,
    update_task_celery_id,
    update_task_status,
    set_task_review_status,
    set_task_rubric_id,
    get_task,
    fetch_results,
    fetch_results_by_task,
    list_pending_review_tasks,
    list_pending_review_task_rows,
    save_rubric,
    get_rubric,
    list_rubrics,
    get_recent_rubric_by_fingerprint,
    append_rubric_generate_audit,
    list_rubric_generate_audit,
    list_hygiene_interceptions,
    get_hygiene_interception_by_id,
    update_hygiene_interception_action,
    bulk_update_hygiene_interception_action,
    create_golden_annotation_asset,
    list_golden_annotation_assets,
    get_annotation_asset_by_id,
    get_task_status_counts,
    get_task_statuses_by_celery_ids,
    list_processing_tasks,
    list_stale_pending_tasks,
    fail_stale_pending_orphan_tasks,
    get_completion_latencies_seconds,
    get_task_volume_stats,
    get_annotation_dataset_stats,
    get_review_queue_stats,
    get_prompt_cache_level_stats,
    get_runtime_telemetry_model_hits,
    get_runtime_telemetry_fallback_stats,
    get_teacher_review_decision_counts,
    list_teacher_review_decisions,
    upsert_teacher_review_decision,
    upsert_prompt_control_state,
    get_prompt_control_state,
    upsert_prompt_ab_config,
    get_prompt_ab_config,
    append_prompt_ops_audit,
    list_prompt_ops_audit,
    get_ops_feature_flags,
    upsert_ops_feature_flags,
    list_ops_release_controls,
    get_ops_release_control,
    upsert_ops_release_control,
    append_ops_fault_drill_report,
    list_ops_fault_drill_reports,
    get_ops_fault_drill_report_by_id,
)
from src.core.drills import run_fault_drill
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
from src.utils.file_parsers import UnsupportedFormatError
from src.core.exceptions import GradingSystemError
from src.api.route_helpers import (
    best_effort_cleanup_stale_pending_orphans as _best_effort_cleanup_stale_pending_orphans,
    compute_source_fingerprint as _compute_source_fingerprint,
    deserialize_json_object as _deserialize_json_object,
    derive_student_ids_from_filenames as _derive_student_ids_from_filenames,
    error_detail as _error_detail,
    fetch_celery_queue_snapshot as _fetch_celery_queue_snapshot,
    is_orphan_local_celery_id as _is_orphan_local_celery_id,
    load_settings_from_env as _load_settings_from_env,
    percentile as _percentile,
    remove_task_from_celery_queue as _remove_task_from_celery_queue,
    request_client_ip as _request_client_ip,
    schema_fields_from_model as _schema_fields_from_model,
    store_upload_file_with_limits as _store_upload_file_with_limits,
    build_pending_review_queue_item as _build_pending_review_queue_item,
    build_review_workbench_sample as _build_review_workbench_sample,
    build_task_insights as _build_task_insights,
    compute_review_priority as _compute_review_priority,
    to_release_control_item as _to_release_control_item,
    to_report_card as _to_report_card,
    validate_annotation_anchor as _validate_annotation_anchor,
    validate_batch_single_page_file as _validate_batch_single_page_file,
    validate_skill_gateway_token as _validate_skill_gateway_token,
)
from src.api.route_models import (
    AnnotationAssetDetailResponse,
    AnnotationAssetListResponse,
    AnnotationFeedbackRequest,
    AnnotationFeedbackResponse,
    ApiContractCatalogResponse,
    BoundingBoxInput,
    CapabilityCatalogResponse,
    CapabilityDomainItem,
    CapabilityEndpointItem,
    ContractFieldItem,
    ContractSchemaItem,
    DatasetPipelineSummaryResponse,
    GradeFlowGuideResponse,
    GradingResultItem,
    GoldenAnnotationAssetItem,
    HygieneActionUpdateRequest,
    HygieneBulkActionUpdateRequest,
    HygieneInterceptionItem,
    LectureSuggestionItem,
    OpsAuditLogItem,
    OpsAuditLogResponse,
    OpsConfigSnapshotResponse,
    OpsFaultDrillHistoryResponse,
    OpsFaultDrillRequest,
    OpsFaultDrillResponse,
    OpsFeatureFlagsRequest,
    OpsFeatureFlagsResponse,
    OpsPromptCatalogItem,
    OpsPromptCatalogResponse,
    OpsProviderSwitchRequest,
    OpsReleaseControlLayerItem,
    OpsReleaseControlListResponse,
    OpsReleaseControlRequest,
    OpsReleaseControlResponse,
    OpsRouterControlRequest,
    OpsRouterControlResponse,
    PendingReviewTaskItem,
    PendingReviewTasksResponse,
    PromptAbConfigRequest,
    PromptControlRequest,
    PromptOpsAuditItem,
    ProviderBenchmarkResponse,
    QueueCleanupResponse,
    QueueDiagnosticsResponse,
    QueueProcessingTaskItem,
    QueueStalePendingItem,
    QueueTaskCleanupResponse,
    ReportCardItem,
    ReportDeductionItem,
    ReviewDecisionItem,
    ReviewDecisionListResponse,
    ReviewDecisionUpsertRequest,
    ReviewFlowGuideResponse,
    ReviewTaskStatusResponse,
    ReviewTaskStatusUpdateRequest,
    ReviewWorkbenchSampleItem,
    ReviewWorkbenchTaskResponse,
    RouterPolicyResponse,
    RubricDetailResponse,
    RubricGenerateAuditItem,
    RubricGenerateAuditResponse,
    RubricGenerateResponse,
    RubricSummaryItem,
    RuntimeDashboardResponse,
    SkillLayoutParseRequest,
    SkillValidationRequest,
    SlaSummaryResponse,
    TaskHistoryItem,
    TaskHistoryResponse,
    TaskInsightHotspotItem,
    TaskInsightsResponse,
    TaskReportResponse,
    TaskResponse,
    TaskStatusResponse,
    TraceProbeResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["grading"])
limiter = Limiter(key_func=get_remote_address)


def _sort_pending_review_items(
    items: List[PendingReviewTaskItem],
    *,
    sort_by: str,
    sort_direction: str,
) -> List[PendingReviewTaskItem]:
    reverse = str(sort_direction).strip().lower() == "desc"
    normalized = str(sort_by).strip().lower()
    if normalized == "priority":
        return sorted(
            items,
            key=lambda item: (
                int(item.priority_rank),
                -(item.review_target_count or 0),
                -(item.max_total_deduction or 0.0),
                item.avg_confidence if item.avg_confidence is not None else 1.0,
                str(item.updated_at or ""),
            ),
        )
    if normalized == "task_id":
        return sorted(items, key=lambda item: str(item.task_id), reverse=reverse)
    if normalized == "created_at":
        return sorted(items, key=lambda item: str(item.created_at or ""), reverse=reverse)
    return sorted(items, key=lambda item: str(item.updated_at or ""), reverse=reverse)


def _build_pending_review_summary(items: List[PendingReviewTaskItem]) -> Dict[str, Any]:
    summary = {
        "pending_task_count": len(items),
        "unreadable_task_count": 0,
        "human_review_task_count": 0,
        "low_confidence_task_count": 0,
        "weak_evidence_task_count": 0,
        "review_target_count": 0,
    }
    for item in items:
        bucket = str(item.priority_bucket or "GENERAL")
        if bucket == "UNREADABLE":
            summary["unreadable_task_count"] += 1
        elif bucket == "HUMAN_REVIEW":
            summary["human_review_task_count"] += 1
        elif bucket == "LOW_CONFIDENCE":
            summary["low_confidence_task_count"] += 1
        elif bucket == "WEAK_EVIDENCE":
            summary["weak_evidence_task_count"] += 1
        summary["review_target_count"] += int(item.review_target_count or 0)
    return summary


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


# --- API Endpoints (Phase 28: Celery-decoupled) ---
@router.post("/rubric/generate", response_model=RubricGenerateResponse, status_code=201)
@limiter.limit("5/minute")
async def generate_rubric_job(
    request: Request,
    files: List[UploadFile] = File(...),
    force_regenerate: bool = Form(False),
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

    source_fingerprint = _compute_source_fingerprint(files_data)
    trace_id = get_trace_id()
    client_ip = _request_client_ip(request)
    user_agent = request.headers.get("user-agent")
    referer = request.headers.get("referer")
    if not force_regenerate:
        cached = await get_recent_rubric_by_fingerprint(
            db_path,
            source_fingerprint=source_fingerprint,
            within_seconds=settings.rubric_dedupe_window_seconds,
        )
        if cached:
            logger.warning(
                "rubric_generation_dedup_hit",
                extra={
                    "extra_fields": {
                        "rubric_id": cached["rubric_id"],
                        "dedupe_window_seconds": settings.rubric_dedupe_window_seconds,
                    }
                },
            )
            row = await get_rubric(db_path, cached["rubric_id"])
            grading_points_count = 0
            if row:
                rubric_payload = json.loads(row["rubric_json"])
                grading_points_count = len(rubric_payload.get("grading_points", []))
            await append_rubric_generate_audit(
                db_path,
                trace_id=trace_id,
                rubric_id=cached["rubric_id"],
                source_fingerprint=source_fingerprint,
                reused_from_cache=True,
                force_regenerate=force_regenerate,
                source_file_count=len(files),
                client_ip=client_ip,
                user_agent=user_agent,
                referer=referer,
            )
            return RubricGenerateResponse(
                rubric_id=cached["rubric_id"],
                question_id=cached.get("question_id"),
                grading_points_count=grading_points_count,
                source_file_count=len(files),
                reused_from_cache=True,
            )

    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    workflow = GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
        skill_service=SkillService(db_path=db_path),
    )

    try:
        rubric: TeacherRubric = await workflow.generate_rubric_pipeline(files_data)
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message=str(exc),
                retryable=False,
                next_action="adjust_file",
            ),
        ) from exc
    except RuntimeError as exc:
        if "PHASE35_CONTRACT_BLOCK" in str(exc):
            detail_text = str(exc)
            raise HTTPException(
                status_code=503,
                detail=_error_detail(
                    error_code="UPSTREAM_UNAVAILABLE",
                    message=f"Rubric generation upstream unavailable: {detail_text}",
                    retryable=True,
                    retry_hint="retry_submit",
                    next_action="retry_upload",
                ),
            ) from exc
        raise
    except GradingSystemError as exc:
        error_text = str(exc)
        if "LLM egress disabled by configuration" in error_text:
            raise HTTPException(
                status_code=503,
                detail=_error_detail(
                    error_code="EGRESS_DISABLED",
                    message=error_text,
                    retryable=False,
                    next_action="enable_llm_egress",
                ),
            ) from exc
        raise HTTPException(
            status_code=503,
            detail=_error_detail(
                error_code="UPSTREAM_UNAVAILABLE",
                message=f"Rubric generation upstream unavailable: {error_text}",
                retryable=True,
                retry_hint="retry_submit",
                next_action="retry_upload",
            ),
        ) from exc
    rubric_id = str(uuid.uuid4())
    await save_rubric(
        db_path,
        rubric_id=rubric_id,
        question_id=rubric.question_id,
        rubric_json=rubric.model_dump(),
        source_fingerprint=source_fingerprint,
    )
    await append_rubric_generate_audit(
        db_path,
        trace_id=trace_id,
        rubric_id=rubric_id,
        source_fingerprint=source_fingerprint,
        reused_from_cache=False,
        force_regenerate=force_regenerate,
        source_file_count=len(files),
        client_ip=client_ip,
        user_agent=user_agent,
        referer=referer,
    )
    return RubricGenerateResponse(
        rubric_id=rubric_id,
        question_id=rubric.question_id,
        grading_points_count=len(rubric.grading_points),
        source_file_count=len(files),
        reused_from_cache=False,
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
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    rubric_id: Optional[str] = Form(default=None),
    student_id: Optional[str] = Form(default=None),
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
    await create_task(db_path, task_id, submitted_count=len(file_refs))

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

    await create_task(db_path, task_id, submitted_count=len(file_refs))

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

    await create_task(db_path, task_id, submitted_count=len(file_refs))

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
            "SELECT COUNT(1) FROM grading_results WHERE task_id = ?",
            (task_id,),
        ) as cursor:
            row = await cursor.fetchone()
            response_data["result_count"] = int((row[0] if row else 0) or 0)

    # Phase 29: Status-specific enrichment
    if task["status"] in ["PENDING", "PROCESSING"]:
        response_data["progress"] = float(task.get("progress") or 0.0)
        eta_value = task.get("eta_seconds")
        response_data["eta_seconds"] = int(eta_value) if eta_value is not None else 60
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
        raise HTTPException(status_code=404, detail="input asset missing")

    media_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
    return FileResponse(asset_path, media_type=media_type, filename=asset_path.name)


@router.get("/tasks/history", response_model=TaskHistoryResponse)
async def get_task_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(default=None, pattern="^(PENDING|PROCESSING|COMPLETED|FAILED)$"),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    where_clause = ""
    params: List[Any] = []
    if status:
        where_clause = "WHERE t.status = ?"
        params.append(status)
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
    sort_by: str = Query(default="priority", pattern="^(priority|updated_at|created_at|task_id)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    priority_bucket: Optional[str] = Query(default=None, pattern="^(UNREADABLE|HUMAN_REVIEW|LOW_CONFIDENCE|WEAK_EVIDENCE|GENERAL)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    raw_rows = await list_pending_review_task_rows(
        db_path,
        task_id=task_id,
        grading_status_filter=status,
    )
    decision_counts = await get_teacher_review_decision_counts(
        db_path,
        task_ids=[str(row.get("task_id") or "") for row in raw_rows],
    )

    enriched_items: List[PendingReviewTaskItem] = []
    for row in raw_rows:
        task_results = await fetch_results_by_task(db_path, str(row.get("task_id") or ""))
        enriched_items.append(
            _build_pending_review_queue_item(
                row,
                task_results,
                reviewed_decision_count=decision_counts.get(str(row.get("task_id") or ""), 0),
            )
        )

    if priority_bucket:
        enriched_items = [
            item for item in enriched_items if str(item.priority_bucket or "") == priority_bucket
        ]

    sorted_items = _sort_pending_review_items(
        enriched_items,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    total_items = len(sorted_items)
    offset = (page - 1) * limit
    paged_items = sorted_items[offset: offset + limit]
    summary = _build_pending_review_summary(sorted_items)
    summary["total_items"] = total_items
    return PendingReviewTasksResponse(page=page, limit=limit, items=paged_items, summary=summary)


@router.get("/review/workbench/{task_id}", response_model=ReviewWorkbenchTaskResponse)
async def get_review_workbench_task(
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
                next_action="refresh_review_queue",
            ),
        )

    rows = await fetch_results_by_task(db_path, task_id)
    decision_rows = await list_teacher_review_decisions(db_path, task_id=task_id, limit=500, offset=0)
    decision_map: Dict[str, ReviewDecisionItem] = {}
    for row in decision_rows:
        row["include_in_dataset"] = bool(row.get("include_in_dataset", 0))
        item = ReviewDecisionItem(**row)
        decision_map[item.sample_id] = item

    samples: List[ReviewWorkbenchSampleItem] = []
    for row in rows:
        sample = _build_review_workbench_sample(
            row,
            task_fallback_reason=task.get("fallback_reason"),
            teacher_decision=decision_map.get(str(row.get("student_id") or "").strip() or str(task_id)),
        )
        samples.append(sample)

    if not samples:
        fallback_sample_id = task_id
        synthetic_rank, synthetic_bucket, synthetic_reason = _compute_review_priority(
            status=str(task.get("grading_status") or "REJECTED_UNREADABLE"),
            requires_human_review=True,
            system_confidence=0.0,
            total_deduction=0.0,
            evidence_count=0,
            fallback_reason=task.get("fallback_reason"),
        )
        samples.append(
            ReviewWorkbenchSampleItem(
                sample_id=fallback_sample_id,
                student_id=None,
                status=str(task.get("grading_status") or "REJECTED_UNREADABLE"),
                is_pass=False,
                total_deduction=0.0,
                overall_feedback=str(task.get("error_message") or task.get("fallback_reason") or "当前任务没有可展示的结构化结果。"),
                system_confidence=0.0,
                requires_human_review=True,
                priority_rank=synthetic_rank,
                priority_bucket=synthetic_bucket,
                review_reason=synthetic_reason,
                teacher_decision=decision_map.get(fallback_sample_id),
            )
        )

    samples.sort(
        key=lambda item: (
            int(item.priority_rank),
            -float(item.total_deduction),
            float(item.system_confidence),
            str(item.student_id or item.sample_id),
        )
    )
    risk_summary = {
        "sample_count": len(samples),
        "reviewed_decision_count": len(decision_map),
        "pending_sample_count": max(len(samples) - len(decision_map), 0),
        "unreadable_count": sum(1 for item in samples if item.priority_bucket == "UNREADABLE"),
        "human_review_count": sum(1 for item in samples if item.priority_bucket == "HUMAN_REVIEW"),
        "low_confidence_count": sum(1 for item in samples if item.priority_bucket == "LOW_CONFIDENCE"),
        "weak_evidence_count": sum(1 for item in samples if item.priority_bucket == "WEAK_EVIDENCE"),
    }
    return ReviewWorkbenchTaskResponse(
        task_id=task_id,
        task_status=str(task.get("status") or "UNKNOWN"),
        review_status=str(task.get("review_status") or "NOT_REQUIRED"),
        samples=samples,
        risk_summary=risk_summary,
    )


@router.get("/review/decisions", response_model=ReviewDecisionListResponse)
async def get_review_decisions(
    task_id: str = Query(...),
    sample_id: Optional[str] = Query(default=None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_teacher_review_decisions(
        db_path,
        task_id=task_id,
        sample_id=sample_id,
        limit=limit,
        offset=offset,
    )
    items: List[ReviewDecisionItem] = []
    for row in rows:
        row["include_in_dataset"] = bool(row.get("include_in_dataset", 0))
        items.append(ReviewDecisionItem(**row))
    return ReviewDecisionListResponse(page=page, limit=limit, items=items)


@router.post("/review/decisions", response_model=ReviewDecisionItem)
async def upsert_review_decision(
    payload: ReviewDecisionUpsertRequest,
    db_path: str = Depends(get_db_path),
):
    task = await get_task(db_path, payload.task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="refresh_review_queue",
            ),
        )

    await upsert_teacher_review_decision(
        db_path,
        task_id=payload.task_id,
        sample_id=payload.sample_id,
        student_id=(payload.student_id.strip() if payload.student_id else None),
        decision=payload.decision,
        final_score=payload.final_score,
        teacher_comment=payload.teacher_comment.strip(),
        include_in_dataset=payload.include_in_dataset,
    )
    rows = await list_teacher_review_decisions(
        db_path,
        task_id=payload.task_id,
        sample_id=payload.sample_id,
        limit=1,
        offset=0,
    )
    if not rows:
        raise HTTPException(status_code=500, detail="review decision persistence failed")
    row = rows[0]
    row["include_in_dataset"] = bool(row.get("include_in_dataset", 0))
    return ReviewDecisionItem(**row)


@router.post("/review/tasks/{task_id}/status", response_model=ReviewTaskStatusResponse)
async def update_review_task_status(
    task_id: str,
    payload: ReviewTaskStatusUpdateRequest,
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
                next_action="refresh_review_queue",
            ),
        )
    await set_task_review_status(db_path, task_id, payload.review_status)
    latest = await get_task(db_path, task_id)
    return ReviewTaskStatusResponse(
        task_id=task_id,
        status=str((latest or task).get("status") or "UNKNOWN"),
        review_status=str((latest or task).get("review_status") or payload.review_status),
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
                    CapabilityEndpointItem(method="POST", path="/api/v1/grade/submit-batch", response_model="TaskResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/grade/{task_id}", response_model="TaskStatusResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/grade-batch/{task_id}", response_model="TaskStatusResponse"),
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
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/queue/diagnostics", response_model="QueueDiagnosticsResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/queue/cleanup-stale", response_model="QueueCleanupResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/queue/cleanup-task", response_model="QueueTaskCleanupResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/rubric/audit", response_model="RubricGenerateAuditResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/control"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/ab-config"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/refresh"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/prompt/invalidate"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/prompt/state"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/prompt/audit", response_model="List[PromptOpsAuditItem]"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/config/snapshot", response_model="OpsConfigSnapshotResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/feature-flags", response_model="OpsFeatureFlagsResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/feature-flags", response_model="OpsFeatureFlagsResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/release/controls", response_model="OpsReleaseControlListResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/release/controls", response_model="OpsReleaseControlResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/provider/switch"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/router/control", response_model="OpsRouterControlResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/prompt/catalog", response_model="OpsPromptCatalogResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/audit/logs", response_model="OpsAuditLogResponse"),
                    CapabilityEndpointItem(method="POST", path="/api/v1/ops/fault-drills/run", response_model="OpsFaultDrillResponse"),
                    CapabilityEndpointItem(method="GET", path="/api/v1/ops/fault-drills/history", response_model="OpsFaultDrillHistoryResponse"),
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
        ContractSchemaItem(schema_name="QueueDiagnosticsResponse", fields=_schema_fields_from_model(QueueDiagnosticsResponse)),
        ContractSchemaItem(schema_name="QueueCleanupResponse", fields=_schema_fields_from_model(QueueCleanupResponse)),
        ContractSchemaItem(schema_name="QueueTaskCleanupResponse", fields=_schema_fields_from_model(QueueTaskCleanupResponse)),
        ContractSchemaItem(schema_name="RubricGenerateAuditItem", fields=_schema_fields_from_model(RubricGenerateAuditItem)),
        ContractSchemaItem(schema_name="RubricGenerateAuditResponse", fields=_schema_fields_from_model(RubricGenerateAuditResponse)),
        ContractSchemaItem(schema_name="PromptControlRequest", fields=_schema_fields_from_model(PromptControlRequest)),
        ContractSchemaItem(schema_name="PromptAbConfigRequest", fields=_schema_fields_from_model(PromptAbConfigRequest)),
        ContractSchemaItem(schema_name="PromptOpsAuditItem", fields=_schema_fields_from_model(PromptOpsAuditItem)),
        ContractSchemaItem(schema_name="OpsProviderSwitchRequest", fields=_schema_fields_from_model(OpsProviderSwitchRequest)),
        ContractSchemaItem(schema_name="OpsRouterControlRequest", fields=_schema_fields_from_model(OpsRouterControlRequest)),
        ContractSchemaItem(schema_name="OpsRouterControlResponse", fields=_schema_fields_from_model(OpsRouterControlResponse)),
        ContractSchemaItem(schema_name="OpsFeatureFlagsRequest", fields=_schema_fields_from_model(OpsFeatureFlagsRequest)),
        ContractSchemaItem(schema_name="OpsFeatureFlagsResponse", fields=_schema_fields_from_model(OpsFeatureFlagsResponse)),
        ContractSchemaItem(schema_name="OpsReleaseControlRequest", fields=_schema_fields_from_model(OpsReleaseControlRequest)),
        ContractSchemaItem(schema_name="OpsReleaseControlResponse", fields=_schema_fields_from_model(OpsReleaseControlResponse)),
        ContractSchemaItem(schema_name="OpsReleaseControlListResponse", fields=_schema_fields_from_model(OpsReleaseControlListResponse)),
        ContractSchemaItem(schema_name="OpsFaultDrillRequest", fields=_schema_fields_from_model(OpsFaultDrillRequest)),
        ContractSchemaItem(schema_name="OpsFaultDrillResponse", fields=_schema_fields_from_model(OpsFaultDrillResponse)),
        ContractSchemaItem(schema_name="OpsFaultDrillHistoryResponse", fields=_schema_fields_from_model(OpsFaultDrillHistoryResponse)),
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
            "RATE_LIMITED",
            "UPSTREAM_UNAVAILABLE",
            "INPUT_REJECTED",
            "BATCH_FILE_TYPE_UNSUPPORTED",
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


@router.get("/ops/queue/diagnostics", response_model=QueueDiagnosticsResponse)
async def get_ops_queue_diagnostics(
    stale_threshold_seconds: int = Query(900, ge=60, le=172800),
    sample_limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    queue_length_raw, queued_task_ids_raw, redis_error = _fetch_celery_queue_snapshot(sample_limit=2000)
    queued_task_ids_raw = queued_task_ids_raw or []
    task_status_map = await get_task_statuses_by_celery_ids(db_path, celery_task_ids=queued_task_ids_raw)
    terminal_statuses = {"COMPLETED", "FAILED"}
    queued_task_ids = [
        task_id
        for task_id in queued_task_ids_raw
        if task_status_map.get(task_id) not in terminal_statuses
    ]
    queued_task_id_set = set(queued_task_ids)
    queue_length = len(queued_task_ids) if queue_length_raw is not None else None
    filtered_terminal_count = len(queued_task_ids_raw) - len(queued_task_ids)

    status_counts = await get_task_status_counts(db_path)
    processing_rows = await list_processing_tasks(db_path, limit=sample_limit)
    stale_pending_rows = await list_stale_pending_tasks(
        db_path,
        timeout_seconds=stale_threshold_seconds,
        limit=sample_limit,
    )

    stale_items: List[QueueStalePendingItem] = []
    stale_summary = {"total": 0, "orphan_local": 0, "queued_waiting": 0, "unknown": 0}
    for row in stale_pending_rows:
        stale_summary["total"] += 1
        celery_task_id = row.get("celery_task_id")
        if _is_orphan_local_celery_id(celery_task_id):
            classification: Literal["orphan_local", "queued_waiting", "unknown"] = "orphan_local"
        elif isinstance(celery_task_id, str) and celery_task_id in queued_task_id_set:
            classification = "queued_waiting"
        else:
            classification = "unknown"
        stale_summary[classification] += 1
        stale_items.append(
            QueueStalePendingItem(
                task_id=str(row.get("task_id") or ""),
                celery_task_id=str(celery_task_id) if celery_task_id is not None else None,
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
                age_seconds=int(row.get("age_seconds") or 0),
                classification=classification,
            )
        )

    processing_items = [
        QueueProcessingTaskItem(
            task_id=str(row.get("task_id") or ""),
            celery_task_id=str(row.get("celery_task_id")) if row.get("celery_task_id") is not None else None,
            progress=float(row.get("progress") or 0.0),
            eta_seconds=int(row.get("eta_seconds")) if row.get("eta_seconds") is not None else None,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            age_seconds=int(row.get("age_seconds")) if row.get("age_seconds") is not None else None,
        )
        for row in processing_rows
    ]

    return QueueDiagnosticsResponse(
        version="1.0",
        stale_threshold_seconds=int(stale_threshold_seconds),
        redis_available=redis_error is None,
        redis_error=redis_error,
        celery_queue_length=queue_length,
        queued_task_ids_sample=queued_task_ids[:sample_limit],
        db_status_counts={str(k): int(v) for k, v in status_counts.items()},
        processing_tasks=processing_items,
        stale_pending_summary=stale_summary,
        stale_pending_sample=stale_items,
        notes=[
            "orphan_local indicates stale pending rows likely created by local fallback/test flow and never consumed by worker.",
            "queued_waiting indicates pending rows still present in Redis celery queue (not zombie).",
            *(
                [f"filtered_terminal_queue_items={filtered_terminal_count} (COMPLETED/FAILED hidden from queue panel)."]
                if filtered_terminal_count > 0
                else []
            ),
        ],
    )


@router.post("/ops/queue/cleanup-stale", response_model=QueueCleanupResponse)
async def cleanup_ops_queue_stale_pending(
    stale_threshold_seconds: int = Query(900, ge=60, le=172800),
    limit: int = Query(200, ge=1, le=2000),
    db_path: str = Depends(get_db_path),
):
    cleaned_task_ids = await fail_stale_pending_orphan_tasks(
        db_path,
        timeout_seconds=stale_threshold_seconds,
        limit=limit,
    )
    return QueueCleanupResponse(
        stale_threshold_seconds=int(stale_threshold_seconds),
        cleaned_count=len(cleaned_task_ids),
        cleaned_task_ids=cleaned_task_ids,
    )


@router.post("/ops/queue/cleanup-task", response_model=QueueTaskCleanupResponse)
async def cleanup_ops_queue_task_by_id(
    task_id: str = Query(..., min_length=8, max_length=128),
    remove_from_queue: bool = Query(True),
    db_path: str = Depends(get_db_path),
):
    normalized_task_id = str(task_id).strip()
    task = await get_task(db_path, normalized_task_id)
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

    previous_status = str(task.get("status") or "")
    removed_from_queue_count = 0
    queue_error: Optional[str] = None
    if remove_from_queue:
        removed_from_queue_count, queue_error = _remove_task_from_celery_queue(normalized_task_id)

    marked_failed = False
    if previous_status in {"PENDING", "PROCESSING"}:
        await update_task_status(
            db_path,
            normalized_task_id,
            "FAILED",
            error="Manual queue cleanup by operator",
            fallback_reason="MANUAL_QUEUE_CLEANUP",
        )
        marked_failed = True

    message = (
        "Task marked as FAILED by operator."
        if marked_failed
        else f"Task already terminal ({previous_status})."
    )
    if queue_error:
        message = f"{message} Queue removal warning: {queue_error[:120]}"

    logger.info(
        "ops_queue_task_cleanup",
        extra={
            "extra_fields": {
                "event": "ops_queue_task_cleanup",
                "task_id": normalized_task_id,
                "previous_status": previous_status,
                "marked_failed": marked_failed,
                "removed_from_queue_count": removed_from_queue_count,
            }
        },
    )
    return QueueTaskCleanupResponse(
        task_id=normalized_task_id,
        existed=True,
        previous_status=previous_status,
        marked_failed=marked_failed,
        removed_from_queue_count=removed_from_queue_count,
        message=message,
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
            "phase35_layout_gate_enabled": False,
            "max_input_tokens": int(settings.prompt_max_input_tokens),
            "reserve_output_tokens": int(settings.prompt_reserve_output_tokens),
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


@router.get("/ops/release/controls", response_model=OpsReleaseControlListResponse)
async def get_ops_release_controls(
    db_path: str = Depends(get_db_path),
):
    rows = await list_ops_release_controls(db_path)
    return OpsReleaseControlListResponse(items=[_to_release_control_item(row) for row in rows])


@router.post("/ops/release/controls", response_model=OpsReleaseControlResponse)
async def set_ops_release_control(
    payload: OpsReleaseControlRequest,
    db_path: str = Depends(get_db_path),
):
    await upsert_ops_release_control(
        db_path,
        layer=payload.layer,
        strategy=payload.strategy,
        rollout_percentage=payload.rollout_percentage,
        target_version=payload.target_version,
        config_json=payload.config,
        rollback_config_json=payload.rollback_config,
    )
    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=payload.operator_id,
        action="ops_release_control_update",
        prompt_key=None,
        payload_json=payload.model_dump(),
    )
    row = await get_ops_release_control(db_path, layer=payload.layer)
    item = _to_release_control_item(row)
    return OpsReleaseControlResponse(
        layer=item.layer,
        strategy=item.strategy,
        rollout_percentage=item.rollout_percentage,
        target_version=item.target_version,
        config=item.config,
        rollback_config=item.rollback_config,
        updated_at=item.updated_at,
    )


@router.post("/ops/fault-drills/run", response_model=OpsFaultDrillResponse)
async def run_ops_fault_drill(
    payload: OpsFaultDrillRequest,
    db_path: str = Depends(get_db_path),
):
    drill_result = await run_fault_drill(drill_type=payload.drill_type, db_path=db_path)
    status_value = str(drill_result.get("status") or "failed")
    if status_value not in {"passed", "failed"}:
        status_value = "failed"
    details_value = drill_result.get("details")
    if not isinstance(details_value, dict):
        details_value = {"raw": details_value}

    report_id = await append_ops_fault_drill_report(
        db_path,
        drill_type=payload.drill_type,
        status=status_value,
        details_json=details_value,
        operator_id=payload.operator_id,
    )
    await append_prompt_ops_audit(
        db_path,
        trace_id=get_trace_id(),
        operator_id=payload.operator_id,
        action="ops_fault_drill_run",
        prompt_key=None,
        payload_json={
            "drill_type": payload.drill_type,
            "status": status_value,
            "report_id": report_id,
        },
    )
    row = await get_ops_fault_drill_report_by_id(db_path, report_id=report_id)
    if row is None:
        raise HTTPException(
            status_code=500,
            detail=_error_detail(
                error_code="INTERNAL_ERROR",
                message="fault drill report not persisted",
                retryable=False,
                next_action="contact_ops_admin",
            ),
        )
    details = _deserialize_json_object(row.get("details_json"))
    return OpsFaultDrillResponse(
        report_id=int(row["id"]),
        drill_type=str(row["drill_type"]),  # type: ignore[arg-type]
        status=str(row["status"]),  # type: ignore[arg-type]
        details=details,
        created_at=row.get("created_at"),
    )


@router.get("/ops/fault-drills/history", response_model=OpsFaultDrillHistoryResponse)
async def get_ops_fault_drill_history(
    drill_type: Optional[str] = Query(default=None, pattern="^(redis_unavailable|model_failure|sse_disconnect|db_pressure)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_ops_fault_drill_reports(
        db_path,
        drill_type=drill_type,
        limit=limit,
        offset=offset,
    )
    items: List[OpsFaultDrillResponse] = []
    for row in rows:
        items.append(
            OpsFaultDrillResponse(
                report_id=int(row["id"]),
                drill_type=str(row["drill_type"]),  # type: ignore[arg-type]
                status=str(row["status"]),  # type: ignore[arg-type]
                details=_deserialize_json_object(row.get("details_json")),
                created_at=row.get("created_at"),
            )
        )
    return OpsFaultDrillHistoryResponse(page=page, limit=limit, items=items)


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


@router.get("/ops/rubric/audit", response_model=RubricGenerateAuditResponse)
async def get_ops_rubric_audit(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_rubric_generate_audit(db_path, limit=limit, offset=offset)
    items = [
        RubricGenerateAuditItem(
            id=int(row["id"]),
            trace_id=str(row.get("trace_id") or ""),
            rubric_id=row.get("rubric_id"),
            source_fingerprint=str(row.get("source_fingerprint") or ""),
            reused_from_cache=bool(row.get("reused_from_cache")),
            force_regenerate=bool(row.get("force_regenerate")),
            source_file_count=int(row.get("source_file_count") or 0),
            client_ip=row.get("client_ip"),
            user_agent=row.get("user_agent"),
            referer=row.get("referer"),
            created_at=row.get("created_at"),
        )
        for row in rows
    ]
    return RubricGenerateAuditResponse(page=page, limit=limit, items=items)


@router.post("/skills/layout/parse")
async def skill_layout_parse(payload: SkillLayoutParseRequest, request: Request):
    """
    Internal skill gateway endpoint.
    Used when SKILL_LAYOUT_PARSER_API_URL points to local service.
    """
    import base64
    _validate_skill_gateway_token(request)

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
async def skill_validate(payload: SkillValidationRequest, request: Request):
    """
    Internal validation gateway endpoint.
    Default implementation is contract-only and returns deterministic structure.
    """
    _validate_skill_gateway_token(request)
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
