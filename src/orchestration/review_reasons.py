"""
Review reason processing for paper grading workflow.

This module handles the logic for determining when human review is required
and managing review reasons throughout the grading process.
"""
from typing import Any
from src.core.constants import (
    NON_REVIEW_EXTRACTION_WARNING_CUES,
    REVIEW_REASON_FILL_BLANK_ALIGNMENT_RISK,
    REVIEW_REASON_LOW_QUALITY_CROP,
    REVIEW_REASON_EXTRACTION_RISK,
    REVIEW_REASON_UNREADABLE_ANSWER,
    REVIEW_REASON_MODEL_REQUESTED,
)
from src.schemas.cognitive_ir import EvaluationReport


def warning_requires_human_review(warning: str) -> bool:
    """Check if a warning should trigger human review."""
    return not any(cue in warning for cue in NON_REVIEW_EXTRACTION_WARNING_CUES)


def review_reasons_from_answer_warnings(warnings: list[str]) -> list[str]:
    """Extract review reasons from answer extraction warnings."""
    reasons: list[str] = []
    for warning in warnings:
        if "FILL_BLANK_ALIGNMENT" in warning:
            if REVIEW_REASON_FILL_BLANK_ALIGNMENT_RISK not in reasons:
                reasons.append(REVIEW_REASON_FILL_BLANK_ALIGNMENT_RISK)
        elif "LOW_QUALITY_CROP" in warning:
            if REVIEW_REASON_LOW_QUALITY_CROP not in reasons:
                reasons.append(REVIEW_REASON_LOW_QUALITY_CROP)
        elif warning_requires_human_review(warning):
            if REVIEW_REASON_EXTRACTION_RISK not in reasons:
                reasons.append(REVIEW_REASON_EXTRACTION_RISK)
    return reasons


def answer_requires_extraction_review(answer: Any) -> bool:
    """Check if an answer requires extraction review."""
    if not isinstance(answer, dict):
        return False
    warnings = answer.get("warnings")
    if not isinstance(warnings, list):
        return False
    return any(warning_requires_human_review(w) for w in warnings)


def review_reasons_from_report(report: EvaluationReport) -> list[str]:
    """Extract review reasons from evaluation report."""
    reasons: list[str] = []
    if report.status == "UNREADABLE":
        reasons.append(REVIEW_REASON_UNREADABLE_ANSWER)
    if report.requires_human_review:
        reasons.append(REVIEW_REASON_MODEL_REQUESTED)
    return reasons


def extend_question_review_reasons(
    per_question_review_reasons: dict[str, list[str]],
    question_id: str,
    new_reasons: list[str],
) -> None:
    """Add review reasons to a question's review reason list."""
    if question_id not in per_question_review_reasons:
        per_question_review_reasons[question_id] = []
    existing = per_question_review_reasons[question_id]
    for reason in new_reasons:
        if reason not in existing:
            existing.append(reason)


def merge_review_reasons(*reason_groups: list[str]) -> list[str]:
    """Merge multiple review reason lists, removing duplicates."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in reason_groups:
        for reason in group:
            if reason not in seen:
                seen.add(reason)
                merged.append(reason)
    return merged
