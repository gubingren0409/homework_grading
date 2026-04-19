import uuid
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.dependencies import get_db_path, limiter
from src.core.config import settings
from src.db.client import (
    set_task_review_status,
    get_task,
    fetch_results,
    fetch_results_by_task,
    list_pending_review_tasks,
    list_pending_review_task_rows,
    list_hygiene_interceptions,
    get_hygiene_interception_by_id,
    update_hygiene_interception_action,
    bulk_update_hygiene_interception_action,
    create_golden_annotation_asset,
    list_golden_annotation_assets,
    get_annotation_asset_by_id,
    get_teacher_review_decision_counts,
    list_teacher_review_decisions,
    upsert_teacher_review_decision,
)
from src.core.trace_context import get_trace_id
from src.core.storage_adapter import storage
from src.api.route_helpers import (
    error_detail as _error_detail,
    request_client_ip as _request_client_ip,
    build_pending_review_queue_item as _build_pending_review_queue_item,
    build_review_workbench_sample as _build_review_workbench_sample,
    compute_review_priority as _compute_review_priority,
    to_report_card as _to_report_card,
    validate_annotation_anchor as _validate_annotation_anchor,
)
from src.api.route_models import (
    AnnotationAssetDetailResponse,
    AnnotationAssetListResponse,
    AnnotationFeedbackRequest,
    AnnotationFeedbackResponse,
    BoundingBoxInput,
    GoldenAnnotationAssetItem,
    HygieneActionUpdateRequest,
    HygieneBulkActionUpdateRequest,
    HygieneInterceptionItem,
    PendingReviewTaskItem,
    PendingReviewTasksResponse,
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
)


logger = logging.getLogger(__name__)
router = APIRouter()

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


