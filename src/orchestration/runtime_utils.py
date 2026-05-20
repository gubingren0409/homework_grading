"""
Runtime profiling and monitoring utilities for paper grading workflow.

This module provides functions for tracking execution time, managing runtime
profiles, and analyzing performance metrics.
"""
import time
from typing import Any
from io import BytesIO
from PIL import Image


def new_runtime_profile() -> dict[str, Any]:
    """Create a new runtime profile dictionary."""
    return {"stages": {}, "spans": []}


def record_stage(runtime_profile: dict[str, Any], stage: str, elapsed: float) -> None:
    """Record elapsed time for a workflow stage."""
    runtime_profile["stages"][stage] = elapsed


def append_runtime_span(
    runtime_profile: dict[str, Any],
    *,
    stage: str,
    context_type: str,
    start: float,
    end: float,
    status: str,
    error_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a runtime span to the profile."""
    span: dict[str, Any] = {
        "stage": stage,
        "context_type": context_type,
        "start": start,
        "end": end,
        "elapsed": end - start,
        "status": status,
    }
    if error_type:
        span["error_type"] = error_type
    if metadata:
        span["metadata"] = metadata
    runtime_profile["spans"].append(span)


def runtime_image_size(image_bytes: bytes) -> dict[str, int]:
    """Get image dimensions from bytes."""
    try:
        img = Image.open(BytesIO(image_bytes))
        return {"width": img.width, "height": img.height}
    except Exception:
        return {"width": 0, "height": 0}


def stage_name_for_context_type(context_type: str) -> str:
    """Map context type to stage name."""
    mapping = {
        "student_paper_pages": "student_page_ocr",
        "answer_regions": "answer_region_ocr",
        "reference_images": "reference_ocr",
    }
    return mapping.get(context_type, context_type)


def classify_runtime_error(exc: Exception) -> str:
    """Classify exception type for runtime tracking."""
    exc_type = type(exc).__name__
    if "Timeout" in exc_type or "timeout" in str(exc).lower():
        return "timeout"
    if "Memory" in exc_type or "memory" in str(exc).lower():
        return "memory"
    if "Connection" in exc_type or "connection" in str(exc).lower():
        return "connection"
    return "unknown"


def first_non_empty(values: Any) -> str | None:
    """Return first non-empty string from a list or single value."""
    if isinstance(values, list):
        for v in values:
            s = str(v or "").strip()
            if s:
                return s
        return None
    s = str(values or "").strip()
    return s if s else None
