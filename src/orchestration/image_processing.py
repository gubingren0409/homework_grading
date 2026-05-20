"""
Image processing configuration and utilities for paper grading workflow.

This module provides functions for determining optimal chunk sizes,
concurrency levels, and timeout values for image processing operations.
"""
from src.core.config import settings


def image_chunk_plan(*, image_count: int, context_type: str) -> tuple[int, int]:
    """
    Determine optimal chunk size and concurrency for image processing.

    Args:
        image_count: Total number of images to process
        context_type: Type of images (student_paper_pages, answer_regions, etc.)

    Returns:
        Tuple of (chunk_size, concurrency)
    """
    if context_type == "student_paper_pages":
        if image_count <= 4:
            return (image_count, 1)
        if image_count <= 8:
            return (4, 2)
        return (4, 3)

    if context_type == "answer_regions":
        if image_count <= 6:
            return (image_count, 1)
        if image_count <= 12:
            return (6, 2)
        if image_count <= 24:
            return (8, 3)
        return (12, 3)

    if context_type == "reference_images":
        if image_count <= 3:
            return (image_count, 1)
        if image_count <= 9:
            return (3, 3)
        return (6, 3)

    # Default fallback
    if image_count <= 4:
        return (image_count, 1)
    return (4, 2)


def desired_answer_region_concurrency(image_count: int) -> int:
    """
    Determine optimal concurrency for answer region processing.

    Args:
        image_count: Number of answer region images

    Returns:
        Recommended concurrency level
    """
    if image_count <= 6:
        return 1
    if image_count <= 12:
        return 2
    return 3


def perception_timeout_seconds_for_context_type(context_type: str) -> float:
    """
    Get timeout value for perception operations based on context type.

    Args:
        context_type: Type of images being processed

    Returns:
        Timeout in seconds
    """
    if context_type == "student_paper_pages":
        return settings.paper_page_ocr_timeout_seconds
    if context_type == "answer_regions":
        return settings.paper_answer_ocr_timeout_seconds
    if context_type == "reference_images":
        return settings.paper_reference_ocr_timeout_seconds
    return 60.0  # Default fallback
