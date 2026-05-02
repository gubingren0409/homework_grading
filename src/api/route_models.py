from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TaskResponse(BaseModel):
    task_id: str
    status: str
    rubric_id: Optional[str] = None
    mode: Optional[str] = None
    submitted_count: Optional[int] = None
    status_endpoint: Optional[str] = None
    stream_endpoint: Optional[str] = None
    suggested_poll_interval_seconds: Optional[int] = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    grading_status: Optional[str] = None
    rubric_id: Optional[str] = None
    review_status: Optional[str] = None
    submitted_count: Optional[int] = None
    result_count: Optional[int] = None
    uploaded_count: Optional[int] = None
    processed_count: Optional[int] = None
    succeeded_count: Optional[int] = None
    rejected_count: Optional[int] = None
    fallback_reason: Optional[str] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    progress: Optional[float] = None
    eta_seconds: Optional[int] = None
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


class ReportDeductionItem(BaseModel):
    reference_element_id: str
    error_type: str = "UNKNOWN"
    suggestion: Optional[str] = None
    evidence_snippet: Optional[str] = None


class ReportInputImageItem(BaseModel):
    name: str
    url: str


class ReportCardItem(BaseModel):
    result_id: int
    student_id: Optional[str] = None
    status: str = "SCORED"
    is_pass: bool
    total_deduction: float
    overall_feedback: str = ""
    system_confidence: float = 0.0
    requires_human_review: bool = False
    deductions: List[ReportDeductionItem] = Field(default_factory=list)
    evidence_snippets: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    input_images: List[ReportInputImageItem] = Field(default_factory=list)


class TaskReportResponse(BaseModel):
    task_id: str
    task_status: str
    cards: List[ReportCardItem] = Field(default_factory=list)


class TaskInsightHotspotItem(BaseModel):
    student_id: Optional[str] = None
    risk_level: str
    error_type: str = "UNKNOWN"
    total_deduction: float = 0.0
    evidence_snippet: Optional[str] = None


class LectureSuggestionItem(BaseModel):
    title: str
    reason: str
    action: str


class TaskInsightsResponse(BaseModel):
    task_id: str
    task_status: str
    error_type_counts: Dict[str, int] = Field(default_factory=dict)
    review_bucket_counts: Dict[str, int] = Field(default_factory=dict)
    hotspots: List[TaskInsightHotspotItem] = Field(default_factory=list)
    lecture_suggestions: List[LectureSuggestionItem] = Field(default_factory=list)


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
    submitted_count: int = 0
    result_count: int = 0
    review_target_count: int = 0
    reviewed_decision_count: int = 0
    avg_confidence: Optional[float] = None
    max_total_deduction: Optional[float] = None
    priority_rank: int = 99
    priority_bucket: str = "GENERAL"
    risk_reason: Optional[str] = None
    top_student_id: Optional[str] = None
    error_message: Optional[str] = None
    fallback_reason: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PendingReviewTasksResponse(BaseModel):
    page: int
    limit: int
    items: List[PendingReviewTaskItem]
    summary: Dict[str, Any] = Field(default_factory=dict)


class TaskHistoryItem(BaseModel):
    task_id: str
    status: str
    grading_status: Optional[str] = None
    review_status: Optional[str] = None
    rubric_id: Optional[str] = None
    submitted_count: int = 0
    progress: float = 0.0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    result_count: int = 0


class TaskHistoryResponse(BaseModel):
    page: int
    limit: int
    items: List[TaskHistoryItem]


class ReviewFlowGuideResponse(BaseModel):
    pending_list_endpoint: str
    task_status_enum: List[str]
    grading_status_enum: List[str]
    notes: List[str]


class GradeFlowGuideResponse(BaseModel):
    submit_endpoint: str
    batch_submit_endpoint: Optional[str] = None
    batch_submit_with_reference_endpoint: Optional[str] = None
    paper_submit_endpoint: Optional[str] = None
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
    source_file_count: int
    reused_from_cache: bool = False


class RubricBundleGenerateResponse(BaseModel):
    bundle_id: str
    paper_id: str
    question_count: int
    source_file_count: int
    bundle_json: Dict[str, Any]


class PaperGradeResponse(BaseModel):
    task_id: str
    bundle_id: str
    paper_id: str
    question_count: int
    report_json: Dict[str, Any]


class RubricGenerateAuditItem(BaseModel):
    id: int
    trace_id: str
    rubric_id: Optional[str] = None
    source_fingerprint: str
    reused_from_cache: bool
    force_regenerate: bool
    source_file_count: int
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    created_at: Optional[str] = None


class RubricGenerateAuditResponse(BaseModel):
    page: int
    limit: int
    items: List[RubricGenerateAuditItem]


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


class ReviewDecisionUpsertRequest(BaseModel):
    task_id: str
    sample_id: str = Field(..., min_length=1)
    student_id: Optional[str] = None
    decision: Literal["CONFIRM_MACHINE", "ADJUST_SCORE", "MARK_UNREADABLE", "ESCALATE"]
    final_score: Optional[float] = Field(default=None, ge=0.0)
    teacher_comment: str = Field(..., min_length=1)
    include_in_dataset: bool = False


class ReviewDecisionItem(BaseModel):
    task_id: str
    sample_id: str
    student_id: Optional[str] = None
    decision: str
    final_score: Optional[float] = None
    teacher_comment: str
    include_in_dataset: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ReviewDecisionListResponse(BaseModel):
    page: int
    limit: int
    items: List[ReviewDecisionItem]


class ReviewTaskStatusUpdateRequest(BaseModel):
    review_status: Literal["PENDING_REVIEW", "REVIEWED"] = "REVIEWED"


class ReviewTaskStatusResponse(BaseModel):
    task_id: str
    status: str
    review_status: str


class ReviewWorkbenchSampleItem(BaseModel):
    sample_id: str
    student_id: Optional[str] = None
    status: str = "SCORED"
    is_pass: bool
    total_deduction: float
    overall_feedback: str = ""
    system_confidence: float = 0.0
    requires_human_review: bool = False
    priority_rank: int = 99
    priority_bucket: str = "GENERAL"
    review_reason: str = ""
    deductions: List[ReportDeductionItem] = Field(default_factory=list)
    evidence_snippets: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    perception_snapshot: Dict[str, Any] = Field(default_factory=dict)
    cognitive_snapshot: Dict[str, Any] = Field(default_factory=dict)
    teacher_decision: Optional[ReviewDecisionItem] = None


class ReviewWorkbenchTaskResponse(BaseModel):
    task_id: str
    task_status: str
    review_status: str
    samples: List[ReviewWorkbenchSampleItem] = Field(default_factory=list)
    risk_summary: Dict[str, Any] = Field(default_factory=dict)


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


class QueueProcessingTaskItem(BaseModel):
    task_id: str
    celery_task_id: Optional[str] = None
    progress: float = 0.0
    eta_seconds: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    age_seconds: Optional[int] = None


class QueueStalePendingItem(BaseModel):
    task_id: str
    celery_task_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    age_seconds: int
    classification: Literal["orphan_local", "queued_waiting", "unknown"]


class QueueDiagnosticsResponse(BaseModel):
    version: str
    stale_threshold_seconds: int
    redis_available: bool
    redis_error: Optional[str] = None
    celery_queue_length: Optional[int] = None
    queued_task_ids_sample: List[str] = Field(default_factory=list)
    db_status_counts: Dict[str, int]
    processing_tasks: List[QueueProcessingTaskItem] = Field(default_factory=list)
    stale_pending_summary: Dict[str, int]
    stale_pending_sample: List[QueueStalePendingItem] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class QueueCleanupResponse(BaseModel):
    stale_threshold_seconds: int
    cleaned_count: int
    cleaned_task_ids: List[str] = Field(default_factory=list)


class QueueTaskCleanupResponse(BaseModel):
    task_id: str
    existed: bool
    previous_status: Optional[str] = None
    marked_failed: bool
    removed_from_queue_count: int = 0
    message: str


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


class OpsReleaseControlLayerItem(BaseModel):
    layer: Literal["api", "prompt", "router"]
    strategy: Literal["stable", "canary", "rollback"]
    rollout_percentage: int
    target_version: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    rollback_config: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None


class OpsReleaseControlListResponse(BaseModel):
    items: List[OpsReleaseControlLayerItem]


class OpsReleaseControlRequest(BaseModel):
    layer: Literal["api", "prompt", "router"]
    strategy: Literal["stable", "canary", "rollback"]
    rollout_percentage: int = Field(..., ge=0, le=100)
    target_version: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    rollback_config: Dict[str, Any] = Field(default_factory=dict)
    operator_id: Optional[str] = None


class OpsReleaseControlResponse(BaseModel):
    layer: Literal["api", "prompt", "router"]
    strategy: Literal["stable", "canary", "rollback"]
    rollout_percentage: int
    target_version: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    rollback_config: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None


class OpsFaultDrillRequest(BaseModel):
    drill_type: Literal["redis_unavailable", "model_failure", "sse_disconnect", "db_pressure"]
    operator_id: Optional[str] = None


class OpsFaultDrillResponse(BaseModel):
    report_id: int
    drill_type: Literal["redis_unavailable", "model_failure", "sse_disconnect", "db_pressure"]
    status: Literal["passed", "failed"]
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class OpsFaultDrillHistoryResponse(BaseModel):
    page: int
    limit: int
    items: List[OpsFaultDrillResponse]

