import uuid
import json
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from celery.exceptions import OperationalError as CeleryOperationalError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from src.api.dependencies import get_db_path
from src.core.config import settings
from src.db.client import (
    update_task_status,
    get_task,
    list_rubric_generate_audit,
    get_task_status_counts,
    get_task_statuses_by_celery_ids,
    list_processing_tasks,
    list_stale_pending_tasks,
    fail_stale_pending_orphan_tasks,
    list_stale_processing_tasks,
    fail_stale_processing_tasks,
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
from src.worker.main import grade_homework_task
from src.core.trace_context import get_trace_id
from src.core.runtime_router import get_runtime_router_controller
from src.core.drills import run_fault_drill
from src.perception.factory import create_perception_engine
from src.prompts.provider import get_prompt_provider
from src.prompts.schemas import PromptInvalidationEvent
from src.api.route_helpers import (
    deserialize_json_object as _deserialize_json_object,
    error_detail as _error_detail,
    fetch_celery_queue_snapshot as _fetch_celery_queue_snapshot,
    is_orphan_local_celery_id as _is_orphan_local_celery_id,
    load_settings_from_env as _load_settings_from_env,
    remove_task_from_celery_queue as _remove_task_from_celery_queue,
    to_release_control_item as _to_release_control_item,
)
from src.api.route_models import (
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
    PromptAbConfigRequest,
    PromptControlRequest,
    PromptOpsAuditItem,
    QueueCleanupResponse,
    QueueDiagnosticsResponse,
    QueueProcessingTaskItem,
    QueueStalePendingItem,
    QueueTaskCleanupResponse,
    RubricGenerateAuditItem,
    RubricGenerateAuditResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter()

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


@router.post("/ops/queue/cleanup-processing")
async def cleanup_ops_queue_stale_processing(
    stale_threshold_seconds: int = Query(600, ge=60, le=172800),
    limit: int = Query(200, ge=1, le=2000),
    db_path: str = Depends(get_db_path),
):
    """P9-07: Force-clean PROCESSING tasks that have exceeded heartbeat timeout."""
    stale_rows = await list_stale_processing_tasks(
        db_path, timeout_seconds=stale_threshold_seconds, limit=limit,
    )
    cleaned_task_ids = await fail_stale_processing_tasks(
        db_path, timeout_seconds=stale_threshold_seconds, limit=limit,
    )
    return {
        "stale_threshold_seconds": int(stale_threshold_seconds),
        "stale_found": len(stale_rows),
        "cleaned_count": len(cleaned_task_ids),
        "cleaned_task_ids": cleaned_task_ids,
        "hint": "Uses last_heartbeat_at when available, falls back to updated_at.",
    }


@router.post("/ops/uploads/cleanup-expired")
async def cleanup_expired_uploads(
    ttl_days: int = Query(7, ge=1, le=365),
):
    """P9-04: Manually trigger TTL-based upload directory cleanup."""
    import time as _time
    import shutil

    uploads_path = settings.uploads_path
    if not uploads_path.is_dir():
        return {"scanned": 0, "cleaned": 0, "errors": 0}

    cutoff_ts = _time.time() - (ttl_days * 86400)
    scanned = 0
    cleaned = 0
    errors = 0
    cleaned_dirs: list[str] = []

    for entry in uploads_path.iterdir():
        if not entry.is_dir():
            continue
        scanned += 1
        try:
            if entry.stat().st_mtime < cutoff_ts:
                shutil.rmtree(entry)
                cleaned += 1
                cleaned_dirs.append(entry.name)
        except Exception:
            errors += 1

    return {
        "ttl_days": ttl_days,
        "scanned": scanned,
        "cleaned": cleaned,
        "errors": errors,
        "cleaned_dirs_sample": cleaned_dirs[:20],
    }


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


