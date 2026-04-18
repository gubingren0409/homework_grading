import hashlib
from typing import Any, List, Tuple

from src.core.config import settings


def project_statuses(report: Any) -> tuple[str, str]:
    grading_status = str(getattr(report, "status", "SCORED"))
    if grading_status == "REJECTED_UNREADABLE":
        return "COMPLETED", "PENDING_REVIEW"
    requires_review = bool(getattr(report, "requires_human_review", False))
    return "COMPLETED", ("PENDING_REVIEW" if requires_review else "NOT_REQUIRED")


def project_batch_task_summary(reports: List[Any]) -> tuple[str, str]:
    if any(str(getattr(r, "status", "SCORED")) == "REJECTED_UNREADABLE" for r in reports):
        return "REJECTED_UNREADABLE", "PENDING_REVIEW"
    if any(bool(getattr(r, "requires_human_review", False)) for r in reports):
        return "SCORED", "PENDING_REVIEW"
    return "SCORED", "NOT_REQUIRED"


def derive_interception_node(report: Any) -> str:
    feedback = str(getattr(report, "overall_feedback", "") or "")
    if "空白卷" in feedback or "未作答" in feedback:
        return "blank"
    return "short_circuit"


def compute_effective_batch_concurrency(total_items: int, configured_concurrency: int) -> int:
    if total_items <= 0:
        return 1
    return max(1, min(total_items, configured_concurrency))


def compute_source_fingerprint(files_data: List[Tuple[bytes, str]]) -> str:
    file_hashes = sorted(hashlib.sha256(content).hexdigest() for content, _ in files_data)
    payload = "\n".join(file_hashes).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def should_emit_batch_progress(
    *,
    completed_count: int,
    total_count: int,
    last_emitted_count: int,
    last_emit_ts: float,
    now_ts: float,
) -> bool:
    if completed_count >= total_count:
        return True
    step = max(1, int(settings.batch_progress_update_step))
    if completed_count - last_emitted_count >= step:
        return True
    min_interval = float(settings.batch_progress_min_interval_seconds)
    return (now_ts - last_emit_ts) >= min_interval

