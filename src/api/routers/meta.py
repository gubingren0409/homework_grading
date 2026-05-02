import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.dependencies import get_db_path
from src.core.config import settings
from src.db.client import (
    get_task,
    get_task_status_counts,
    get_completion_latencies_seconds,
    get_task_volume_stats,
    get_annotation_dataset_stats,
    get_review_queue_stats,
    get_prompt_cache_level_stats,
    get_runtime_telemetry_model_hits,
    get_runtime_telemetry_fallback_stats,
)
from src.core.trace_context import get_trace_id
from src.worker.main import emit_trace_probe
from src.core.runtime_router import get_runtime_router_controller
from src.api.route_helpers import (
    percentile as _percentile,
    schema_fields_from_model as _schema_fields_from_model,
)
from src.api.route_models import (
    AnnotationAssetDetailResponse,
    AnnotationAssetListResponse,
    AnnotationFeedbackRequest,
    AnnotationFeedbackResponse,
    ApiContractCatalogResponse,
    CapabilityCatalogResponse,
    CapabilityDomainItem,
    CapabilityEndpointItem,
    ContractFieldItem,
    ContractSchemaItem,
    DatasetPipelineSummaryResponse,
    GradeFlowGuideResponse,
    GradingResultItem,
    GoldenAnnotationAssetItem,
    HygieneInterceptionItem,
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
    QueueTaskCleanupResponse,
    ReviewFlowGuideResponse,
    RouterPolicyResponse,
    RubricDetailResponse,
    RubricGenerateAuditItem,
    RubricGenerateAuditResponse,
    RubricGenerateResponse,
    RubricSummaryItem,
    RuntimeDashboardResponse,
    SlaSummaryResponse,
    TaskResponse,
    TaskStatusResponse,
    TraceProbeResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter()

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
            "fallback_model": settings.deepseek_fallback_model_name,
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
            "fallback_model": settings.deepseek_fallback_model_name,
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


