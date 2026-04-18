import asyncio
import hashlib
import json
import logging
import math
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request, UploadFile
from pydantic import BaseModel

from src.core.config import settings
from src.core.storage_adapter import storage
from src.db.client import fail_stale_pending_orphan_tasks

from .route_models import (
    AnnotationFeedbackRequest,
    BoundingBoxInput,
    ContractFieldItem,
    LectureSuggestionItem,
    OpsReleaseControlLayerItem,
    PendingReviewTaskItem,
    ReportCardItem,
    ReportDeductionItem,
    ReportInputImageItem,
    ReviewDecisionItem,
    ReviewWorkbenchSampleItem,
    TaskInsightHotspotItem,
)


logger = logging.getLogger(__name__)


def error_detail(
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


async def store_upload_file_with_limits(task_id: str, upload: UploadFile) -> str:
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
                        detail=error_detail(
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
                        detail=error_detail(
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


def derive_student_ids_from_filenames(files: List[UploadFile]) -> List[str]:
    seen: Dict[str, int] = {}
    student_ids: List[str] = []
    for idx, upload in enumerate(files, start=1):
        raw_name = upload.filename or f"student_{idx}.jpg"
        stem = Path(raw_name).stem.strip() or f"student_{idx}"
        count = seen.get(stem, 0) + 1
        seen[stem] = count
        student_ids.append(stem if count == 1 else f"{stem}_{count}")
    return student_ids


def validate_batch_single_page_file(upload: UploadFile) -> None:
    filename = upload.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                error_code="BATCH_FILE_TYPE_UNSUPPORTED",
                message="Batch mode accepts image files only (.jpg/.jpeg/.png).",
                retryable=False,
                next_action="adjust_file",
            ),
        )


def is_orphan_local_celery_id(celery_task_id: Optional[str]) -> bool:
    if celery_task_id is None:
        return True
    normalized = str(celery_task_id).strip()
    if not normalized:
        return True
    if normalized.startswith("local:"):
        return True
    if normalized == "mock-celery-id":
        return True
    return False


def fetch_celery_queue_snapshot(sample_limit: int = 200) -> tuple[Optional[int], Optional[List[str]], Optional[str]]:
    try:
        import redis

        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        queue_name = "celery"
        queue_length = int(client.llen(queue_name))
        safe_limit = max(1, int(sample_limit))
        raw_items = client.lrange(queue_name, 0, safe_limit - 1)

        task_ids: List[str] = []
        for raw in raw_items:
            payload_text: Optional[str] = None
            if isinstance(raw, bytes):
                payload_text = raw.decode("utf-8", errors="ignore")
            elif isinstance(raw, str):
                payload_text = raw
            if not payload_text:
                continue
            try:
                envelope = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            headers = envelope.get("headers") if isinstance(envelope, dict) else None
            task_id = headers.get("id") if isinstance(headers, dict) else None
            if isinstance(task_id, str) and task_id:
                task_ids.append(task_id)

        return queue_length, task_ids, None
    except Exception as exc:
        return None, None, str(exc)


def remove_task_from_celery_queue(task_id: str, queue_name: str = "celery") -> tuple[int, Optional[str]]:
    try:
        import redis

        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        raw_items = client.lrange(queue_name, 0, -1)
        removed_count = 0
        for raw in raw_items:
            payload_text: Optional[str] = None
            if isinstance(raw, bytes):
                payload_text = raw.decode("utf-8", errors="ignore")
            elif isinstance(raw, str):
                payload_text = raw
            if not payload_text:
                continue
            try:
                envelope = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            headers = envelope.get("headers") if isinstance(envelope, dict) else None
            queue_task_id = headers.get("id") if isinstance(headers, dict) else None
            if isinstance(queue_task_id, str) and queue_task_id == task_id:
                removed_count += int(client.lrem(queue_name, 1, raw))
        return removed_count, None
    except Exception as exc:
        return 0, str(exc)


async def best_effort_cleanup_stale_pending_orphans(db_path: str) -> None:
    try:
        cleaned_task_ids = await fail_stale_pending_orphan_tasks(
            db_path,
            timeout_seconds=settings.pending_orphan_timeout_seconds,
            limit=200,
        )
        if cleaned_task_ids:
            logger.warning(
                "stale_pending_orphans_cleaned",
                extra={
                    "extra_fields": {
                        "event": "stale_pending_orphans_cleaned",
                        "cleaned_count": len(cleaned_task_ids),
                        "sample_task_ids": cleaned_task_ids[:10],
                    }
                },
            )
    except Exception as exc:
        logger.warning(
            "stale_pending_orphan_cleanup_failed",
            extra={
                "extra_fields": {
                    "event": "stale_pending_orphan_cleanup_failed",
                    "reason": str(exc),
                }
            },
        )


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    sorted_values = sorted(values)
    idx = int(round((len(sorted_values) - 1) * p))
    idx = max(0, min(idx, len(sorted_values) - 1))
    return float(sorted_values[idx])


def schema_fields_from_model(model: type[BaseModel]) -> List[ContractFieldItem]:
    fields: List[ContractFieldItem] = []
    for name, field in model.model_fields.items():
        fields.append(
            ContractFieldItem(
                name=name,
                type=str(field.annotation),
                required=field.is_required(),
            )
        )
    return fields


def load_settings_from_env() -> None:
    settings.__dict__.clear()
    refreshed = settings.__class__()  # type: ignore[call-arg]
    settings.__dict__.update(refreshed.__dict__)


def validate_skill_gateway_token(request: Request) -> None:
    if not settings.skill_gateway_auth_enabled:
        return
    expected = settings.skill_gateway_auth_token
    if not expected:
        raise HTTPException(status_code=503, detail="skill gateway auth misconfigured")
    received = request.headers.get("X-Skill-Gateway-Token")
    if received != expected:
        raise HTTPException(status_code=403, detail="skill gateway unauthorized")


def validate_bbox_in_image_space(*, bbox: BoundingBoxInput, image_width: int, image_height: int) -> List[float]:
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


def extract_layout_region_from_snapshot(perception_ir_snapshot: Dict[str, Any], region_id: str) -> Optional[Dict[str, Any]]:
    regions = perception_ir_snapshot.get("regions")
    if not isinstance(regions, list):
        return None
    for item in regions:
        if isinstance(item, dict) and str(item.get("target_id")) == region_id:
            return item
    return None


def bbox_from_snapshot_region(region: Dict[str, Any], *, image_width: int, image_height: int) -> List[float]:
    bbox = region.get("bbox")
    if not isinstance(bbox, dict):
        raise HTTPException(status_code=422, detail="perception_ir_snapshot region bbox must be object")

    try:
        x_min = float(bbox["x_min"])
        y_min = float(bbox["y_min"])
        x_max = float(bbox["x_max"])
        y_max = float(bbox["y_max"])
    except Exception as exc:
        raise HTTPException(status_code=422, detail="perception_ir_snapshot bbox malformed") from exc

    return [x_min * image_width, y_min * image_height, x_max * image_width, y_max * image_height]


def validate_annotation_anchor(payload: AnnotationFeedbackRequest) -> List[float]:
    bbox_abs = validate_bbox_in_image_space(
        bbox=payload.bbox,
        image_width=payload.image_width,
        image_height=payload.image_height,
    )

    region = extract_layout_region_from_snapshot(payload.perception_ir_snapshot, payload.region_id)
    if region is not None:
        source_region_type = str(region.get("region_type") or "")
        if source_region_type != payload.region_type:
            raise HTTPException(status_code=422, detail="region_type mismatch against perception_ir_snapshot")

        source_bbox_abs = bbox_from_snapshot_region(
            region,
            image_width=payload.image_width,
            image_height=payload.image_height,
        )
        sx1, sy1, sx2, sy2 = source_bbox_abs
        x1, y1, x2, y2 = bbox_abs
        if x1 < sx1 or y1 < sy1 or x2 > sx2 or y2 > sy2:
            raise HTTPException(status_code=422, detail="bbox is outside source perception region")

    evaluations = payload.cognitive_ir_snapshot.get("step_evaluations")
    if isinstance(evaluations, list):
        if region is not None:
            has_region_anchor = any(
                isinstance(item, dict) and str(item.get("reference_element_id")) == payload.region_id
                for item in evaluations
            )
            if not has_region_anchor:
                raise HTTPException(status_code=422, detail="cognitive_ir_snapshot lacks region_id anchor")
        else:
            has_any_anchor = any(
                isinstance(item, dict) and str(item.get("reference_element_id") or "").strip()
                for item in evaluations
            )
            if not has_any_anchor:
                raise HTTPException(status_code=422, detail="cognitive_ir_snapshot.step_evaluations has no anchor")
    else:
        raise HTTPException(status_code=422, detail="cognitive_ir_snapshot.step_evaluations missing")

    return bbox_abs


def compute_source_fingerprint(files_data: List[tuple[bytes, str]]) -> str:
    file_hashes = sorted(hashlib.sha256(content).hexdigest() for content, _ in files_data)
    payload = "\n".join(file_hashes).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def extract_evidence_lookup(perception_payload: Any) -> Dict[str, str]:
    if not isinstance(perception_payload, dict):
        return {}
    elements = perception_payload.get("elements")
    if not isinstance(elements, list):
        return {}
    lookup: Dict[str, str] = {}
    for elem in elements:
        if not isinstance(elem, dict):
            continue
        ref_id = str(elem.get("element_id") or "").strip()
        raw = str(elem.get("raw_content") or "").strip()
        if ref_id and raw:
            lookup[ref_id] = raw[:160]
    return lookup


def parse_report_payload(raw_report: Any) -> Dict[str, Any]:
    parsed: Any = raw_report
    if isinstance(raw_report, str):
        try:
            parsed = json.loads(raw_report)
        except Exception:
            parsed = {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def extract_evaluation_payload(parsed_report: Dict[str, Any]) -> Dict[str, Any]:
    evaluation = parsed_report.get("evaluation_report") if isinstance(parsed_report.get("evaluation_report"), dict) else parsed_report
    if not isinstance(evaluation, dict):
        return {}
    return evaluation


def to_report_card(row: Dict[str, Any]) -> ReportCardItem:
    raw_report = row.get("report_json")
    parsed = parse_report_payload(raw_report)
    evaluation = extract_evaluation_payload(parsed)
    perception_payload = parsed.get("perception_output") if isinstance(parsed.get("perception_output"), dict) else {}
    evidence_lookup = extract_evidence_lookup(perception_payload)
    input_file_refs = parsed.get("input_file_refs") if isinstance(parsed.get("input_file_refs"), list) else []
    input_filenames = parsed.get("input_filenames") if isinstance(parsed.get("input_filenames"), list) else []

    deductions: List[ReportDeductionItem] = []
    suggestions: List[str] = []
    evidence_snippets: List[str] = []
    input_images: List[ReportInputImageItem] = []
    step_items = evaluation.get("step_evaluations")
    if isinstance(step_items, list):
        for step in step_items:
            if not isinstance(step, dict) or bool(step.get("is_correct", False)):
                continue
            ref_id = str(step.get("reference_element_id") or "").strip()
            suggestion = str(step.get("correction_suggestion") or "").strip() or None
            evidence = evidence_lookup.get(ref_id)
            deductions.append(
                ReportDeductionItem(
                    reference_element_id=ref_id or "unknown",
                    error_type=str(step.get("error_type") or "UNKNOWN"),
                    suggestion=suggestion,
                    evidence_snippet=evidence,
                )
            )
            if suggestion:
                suggestions.append(suggestion)
            if evidence:
                evidence_snippets.append(evidence)

    result_id = int(row.get("id") or 0)
    for idx, file_ref in enumerate(input_file_refs):
        if not isinstance(file_ref, str) or not file_ref.strip():
            continue
        fallback_name = f"input_{idx + 1}"
        name = input_filenames[idx] if idx < len(input_filenames) and isinstance(input_filenames[idx], str) else fallback_name
        input_images.append(
            ReportInputImageItem(
                name=str(name or fallback_name),
                url=f"/api/v1/results/{result_id}/inputs/{idx}",
            )
        )

    return ReportCardItem(
        result_id=result_id,
        student_id=row.get("student_id"),
        status=str(evaluation.get("status") or "SCORED"),
        is_pass=bool(evaluation.get("is_fully_correct", row.get("is_pass", False))),
        total_deduction=float(evaluation.get("total_score_deduction", row.get("total_deduction", 0.0))),
        overall_feedback=str(evaluation.get("overall_feedback") or ""),
        system_confidence=float(evaluation.get("system_confidence", 0.0) or 0.0),
        requires_human_review=bool(evaluation.get("requires_human_review", False)),
        deductions=deductions,
        evidence_snippets=list(dict.fromkeys(evidence_snippets)),
        suggestions=list(dict.fromkeys(suggestions)),
        input_images=input_images,
    )


def compute_review_priority(
    *,
    status: str,
    requires_human_review: bool,
    system_confidence: float,
    total_deduction: float,
    evidence_count: int,
    fallback_reason: Optional[str] = None,
) -> tuple[int, str, str]:
    normalized_status = str(status or "").upper()
    normalized_fallback = str(fallback_reason or "").upper()
    if normalized_status == "REJECTED_UNREADABLE" or "SHORT_CIRCUIT" in normalized_fallback:
        return 1, "UNREADABLE", "机器拒判或感知短路，优先人工接管。"
    if requires_human_review:
        return 2, "HUMAN_REVIEW", "模型已明确标记需要人工复核。"
    if system_confidence < 0.75:
        return 3, "LOW_CONFIDENCE", "系统置信度偏低，建议优先抽检。"
    if total_deduction > 0 and evidence_count == 0:
        return 4, "WEAK_EVIDENCE", "存在扣分但证据片段不足，建议复核。"
    return 5, "GENERAL", "常规人工抽检样本。"


def build_pending_review_queue_item(
    task_row: Dict[str, Any],
    result_rows: List[Dict[str, Any]],
    *,
    reviewed_decision_count: int = 0,
) -> PendingReviewTaskItem:
    cards = [to_report_card(row) for row in result_rows]
    result_count = len(result_rows)
    if not cards:
        priority_rank, priority_bucket, risk_reason = compute_review_priority(
            status=str(task_row.get("grading_status") or "REJECTED_UNREADABLE"),
            requires_human_review=True,
            system_confidence=0.0,
            total_deduction=0.0,
            evidence_count=0,
            fallback_reason=task_row.get("fallback_reason"),
        )
        review_target_count = 1
        avg_confidence = 0.0
        max_total_deduction = 0.0
        top_student_id = None
    else:
        sample_summaries: List[Dict[str, Any]] = []
        for card in cards:
            priority_rank, priority_bucket, risk_reason = compute_review_priority(
                status=card.status,
                requires_human_review=card.requires_human_review,
                system_confidence=card.system_confidence,
                total_deduction=card.total_deduction,
                evidence_count=len(card.evidence_snippets),
                fallback_reason=task_row.get("fallback_reason"),
            )
            sample_summaries.append(
                {
                    "student_id": card.student_id,
                    "priority_rank": priority_rank,
                    "priority_bucket": priority_bucket,
                    "risk_reason": risk_reason,
                    "system_confidence": card.system_confidence,
                    "total_deduction": card.total_deduction,
                    "review_target": priority_rank < 5,
                }
            )
        sample_summaries.sort(
            key=lambda item: (
                item["priority_rank"],
                -float(item["total_deduction"]),
                float(item["system_confidence"]),
                str(item.get("student_id") or ""),
            )
        )
        top = sample_summaries[0]
        priority_rank = int(top["priority_rank"])
        priority_bucket = str(top["priority_bucket"])
        risk_reason = str(top["risk_reason"])
        review_target_count = sum(1 for item in sample_summaries if bool(item["review_target"]))
        avg_confidence = sum(float(card.system_confidence) for card in cards) / len(cards)
        max_total_deduction = max(float(card.total_deduction) for card in cards)
        top_student_id = top.get("student_id")

    return PendingReviewTaskItem(
        task_id=str(task_row.get("task_id") or ""),
        status=str(task_row.get("status") or ""),
        grading_status=(str(task_row.get("grading_status")) if task_row.get("grading_status") is not None else None),
        rubric_id=(str(task_row.get("rubric_id")) if task_row.get("rubric_id") is not None else None),
        review_status=str(task_row.get("review_status") or "PENDING_REVIEW"),
        submitted_count=int(task_row.get("submitted_count") or 0),
        result_count=result_count,
        review_target_count=int(review_target_count),
        reviewed_decision_count=int(reviewed_decision_count),
        avg_confidence=float(avg_confidence) if avg_confidence is not None else None,
        max_total_deduction=float(max_total_deduction) if max_total_deduction is not None else None,
        priority_rank=int(priority_rank),
        priority_bucket=priority_bucket,
        risk_reason=risk_reason,
        top_student_id=(str(top_student_id) if top_student_id else None),
        error_message=(str(task_row.get("error_message")) if task_row.get("error_message") is not None else None),
        fallback_reason=(str(task_row.get("fallback_reason")) if task_row.get("fallback_reason") is not None else None),
        created_at=(str(task_row.get("created_at")) if task_row.get("created_at") is not None else None),
        updated_at=(str(task_row.get("updated_at")) if task_row.get("updated_at") is not None else None),
    )


def build_review_workbench_sample(
    row: Dict[str, Any],
    *,
    task_fallback_reason: Optional[str] = None,
    teacher_decision: Optional[ReviewDecisionItem] = None,
) -> ReviewWorkbenchSampleItem:
    parsed = parse_report_payload(row.get("report_json"))
    card = to_report_card(row)
    perception_snapshot = parsed.get("perception_ir_snapshot")
    if not isinstance(perception_snapshot, dict):
        perception_snapshot = parsed.get("perception_output") if isinstance(parsed.get("perception_output"), dict) else {}
    cognitive_snapshot = parsed.get("cognitive_ir_snapshot")
    if not isinstance(cognitive_snapshot, dict):
        cognitive_snapshot = extract_evaluation_payload(parsed)
    priority_rank, priority_bucket, review_reason = compute_review_priority(
        status=card.status,
        requires_human_review=card.requires_human_review,
        system_confidence=card.system_confidence,
        total_deduction=card.total_deduction,
        evidence_count=len(card.evidence_snippets),
        fallback_reason=task_fallback_reason,
    )
    student_id = str(row.get("student_id") or "").strip() or None
    sample_id = student_id or str(row.get("task_id") or "").strip() or "task-sample"
    return ReviewWorkbenchSampleItem(
        sample_id=sample_id,
        student_id=student_id,
        status=card.status,
        is_pass=card.is_pass,
        total_deduction=card.total_deduction,
        overall_feedback=card.overall_feedback,
        system_confidence=card.system_confidence,
        requires_human_review=card.requires_human_review,
        priority_rank=priority_rank,
        priority_bucket=priority_bucket,
        review_reason=review_reason,
        deductions=card.deductions,
        evidence_snippets=card.evidence_snippets,
        suggestions=card.suggestions,
        perception_snapshot=perception_snapshot,
        cognitive_snapshot=cognitive_snapshot,
        teacher_decision=teacher_decision,
    )


def build_task_insights(cards: List[ReportCardItem]) -> Dict[str, Any]:
    error_type_counts: Dict[str, int] = {}
    review_bucket_counts = {
        "rejected_unreadable": 0,
        "requires_human_review": 0,
        "low_confidence": 0,
        "weak_evidence": 0,
    }
    hotspots: List[TaskInsightHotspotItem] = []

    for card in cards:
        if card.status == "REJECTED_UNREADABLE":
            review_bucket_counts["rejected_unreadable"] += 1
        if card.requires_human_review:
            review_bucket_counts["requires_human_review"] += 1
        if card.system_confidence < 0.75:
            review_bucket_counts["low_confidence"] += 1
        if card.total_deduction > 0 and not card.evidence_snippets:
            review_bucket_counts["weak_evidence"] += 1

        if card.deductions:
            for deduction in card.deductions:
                key = str(deduction.error_type or "UNKNOWN")
                error_type_counts[key] = error_type_counts.get(key, 0) + 1
            first = card.deductions[0]
            hotspots.append(
                TaskInsightHotspotItem(
                    student_id=card.student_id,
                    risk_level="HIGH" if card.requires_human_review or card.status == "REJECTED_UNREADABLE" else "MEDIUM",
                    error_type=str(first.error_type or "UNKNOWN"),
                    total_deduction=float(card.total_deduction),
                    evidence_snippet=first.evidence_snippet,
                )
            )
        elif card.status == "REJECTED_UNREADABLE":
            hotspots.append(
                TaskInsightHotspotItem(
                    student_id=card.student_id,
                    risk_level="HIGH",
                    error_type="UNREADABLE",
                    total_deduction=float(card.total_deduction),
                    evidence_snippet=None,
                )
            )

    hotspots.sort(key=lambda item: (-float(item.total_deduction), str(item.student_id or "")))
    hotspots = hotspots[:6]

    suggestions: List[LectureSuggestionItem] = []
    dominant = sorted(error_type_counts.items(), key=lambda item: (-item[1], item[0]))
    if dominant:
        top_error, top_count = dominant[0]
        if top_error == "CONCEPTUAL":
            suggestions.append(
                LectureSuggestionItem(
                    title="先讲核心概念，再回到题目",
                    reason=f"概念错误出现 {top_count} 次，是当前班级最主要的问题类型。",
                    action="讲评时先统一澄清定义、方向判断或物理意义，再带学生回到本题的标准解法。",
                )
            )
        elif top_error == "LOGIC":
            suggestions.append(
                LectureSuggestionItem(
                    title="重点复盘解题链路",
                    reason=f"逻辑错误出现 {top_count} 次，说明学生常在推导顺序或条件使用上断链。",
                    action="讲评时按“已知条件 -> 中间推导 -> 最终结论”逐步回放，并强调每一步为什么成立。",
                )
            )
        elif top_error == "CALCULATION":
            suggestions.append(
                LectureSuggestionItem(
                    title="单独抽出计算规范复盘",
                    reason=f"计算错误出现 {top_count} 次，说明学生更容易在运算细节上失分。",
                    action="讲评时单独整理易错算式、单位换算和符号处理，帮助学生建立检查清单。",
                )
            )
        else:
            suggestions.append(
                LectureSuggestionItem(
                    title="围绕高频错误做专项提醒",
                    reason=f"{top_error} 类型错误当前最常见，共出现 {top_count} 次。",
                    action="讲评时先展示典型错误样例，再说明正确写法和容易混淆的地方。",
                )
            )

    if review_bucket_counts["rejected_unreadable"] > 0:
        suggestions.append(
            LectureSuggestionItem(
                title="补充一次作答规范提醒",
                reason=f"当前有 {review_bucket_counts['rejected_unreadable']} 份样本出现拒判或不可读问题。",
                action="在讲评前补充一次答题拍照、书写清晰度或扫描规范提醒，减少下一轮无效批改。",
            )
        )

    if review_bucket_counts["low_confidence"] > 0:
        suggestions.append(
            LectureSuggestionItem(
                title="先抽检低置信度样本",
                reason=f"当前有 {review_bucket_counts['low_confidence']} 份样本置信度偏低。",
                action="讲评准备时优先人工确认这些样本，避免把不稳定结论直接带进课堂反馈。",
            )
        )

    if not suggestions:
        suggestions.append(
            LectureSuggestionItem(
                title="本题整体表现稳定",
                reason="当前没有明显的高频错因或高风险信号。",
                action="可直接围绕标准答案和少量个别失分样本做精简讲评。",
            )
        )

    return {
        "error_type_counts": error_type_counts,
        "review_bucket_counts": review_bucket_counts,
        "hotspots": hotspots,
        "lecture_suggestions": suggestions,
    }


def request_client_ip(request: Request) -> Optional[str]:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    if request.client:
        return request.client.host
    return None


def deserialize_json_object(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def to_release_control_item(row: Dict[str, Any]) -> OpsReleaseControlLayerItem:
    return OpsReleaseControlLayerItem(
        layer=str(row.get("layer") or "api"),  # type: ignore[arg-type]
        strategy=str(row.get("strategy") or "stable"),  # type: ignore[arg-type]
        rollout_percentage=int(row.get("rollout_percentage") or 100),
        target_version=row.get("target_version"),
        config=deserialize_json_object(row.get("config_json")),
        rollback_config=deserialize_json_object(row.get("rollback_config_json")),
        updated_at=row.get("updated_at"),
    )

