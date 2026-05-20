"""
Centralized constants for the homework grading system.

This module contains all hardcoded constants that were previously scattered
across the codebase. Centralizing them here makes them easier to maintain
and configure.
"""
import re

# ============================================================================
# Image Processing Constants
# ============================================================================

# Minimum short side dimension for Qwen OCR processing
MIN_QWEN_OCR_SHORT_SIDE = 400

# Low quality crop detection thresholds
LOW_QUALITY_CROP_MIN_SHORT_SIDE = 48
LOW_QUALITY_CROP_MAX_ASPECT_RATIO = 12.0


# ============================================================================
# Review Reason Constants
# ============================================================================

# Answer region issues
REVIEW_REASON_MISSING_REGION = "MISSING_ANSWER_REGION"
REVIEW_REASON_ANCESTOR_REGION_FALLBACK = "ANCESTOR_REGION_FALLBACK"
REVIEW_REASON_UNMATCHED_REGION = "UNMATCHED_REGION_WITHOUT_RUBRIC"

# Extraction and OCR issues
REVIEW_REASON_EXTRACTION_RISK = "ANSWER_EXTRACTION_RISK"
REVIEW_REASON_FILL_BLANK_ALIGNMENT_RISK = "FILL_BLANK_ALIGNMENT_RISK"
REVIEW_REASON_UNREADABLE_ANSWER = "UNREADABLE_ANSWER"
REVIEW_REASON_LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"
REVIEW_REASON_LOW_QUALITY_CROP = "LOW_QUALITY_CROP"

# Grading issues
REVIEW_REASON_NUMERIC_EQUIVALENCE = "NUMERIC_EQUIVALENCE_CONTRADICTION"
REVIEW_REASON_MODEL_REQUESTED = "MODEL_REQUESTED_HUMAN_REVIEW"

# Timeout and budget issues
REVIEW_REASON_LAYOUT_TIMEOUT = "LAYOUT_TIMEOUT_REVIEW"
REVIEW_REASON_LAYOUT_BUDGET_LIMIT = "LAYOUT_BUDGET_LIMIT_REVIEW"
REVIEW_REASON_PAGE_OCR_TIMEOUT = "PAGE_OCR_TIMEOUT_REVIEW"
REVIEW_REASON_PAGE_OCR_BUDGET_LIMIT = "PAGE_OCR_BUDGET_LIMIT_REVIEW"
REVIEW_REASON_ANSWER_OCR_TIMEOUT = "ANSWER_OCR_TIMEOUT_REVIEW"
REVIEW_REASON_ANSWER_OCR_BUDGET_LIMIT = "ANSWER_OCR_BUDGET_LIMIT_REVIEW"
REVIEW_REASON_COGNITIVE_TIMEOUT = "COGNITIVE_TIMEOUT_REVIEW"
REVIEW_REASON_COGNITIVE_BUDGET_LIMIT = "COGNITIVE_BUDGET_LIMIT_REVIEW"

# Non-review extraction warning cues
NON_REVIEW_EXTRACTION_WARNING_CUES = (
    "ANSWER_TEXT_INFERRED_WITHOUT_STUDENT_TAGS",
    "ANSWER_TEXT_INFERRED_FROM_CHOICE_MARKS",
)


# ============================================================================
# Text Pattern Constants
# ============================================================================

# Regex for numeric tokens
NUMERIC_TOKEN_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")

# Regex for fill-in-the-blank patterns
FILL_BLANK_RE = re.compile(r"_{2,}|＿{2,}")

# Chinese phrases indicating numeric contradictions
NUMERIC_CONTRADICTION_CUES = ("不等", "不相等", "不成立", "不正确", "错误", "不符")


# ============================================================================
# API and Network Constants
# ============================================================================

# Default API base URL for testing (can be overridden by environment)
DEFAULT_TEST_API_BASE = "http://localhost:8000/api/v1"


# ============================================================================
# File and Storage Constants
# ============================================================================

# Maximum evidence snippet length for display
MAX_EVIDENCE_SNIPPET_LENGTH = 240


# ============================================================================
# Grading Workflow Constants
# ============================================================================

# Minimum confidence score for OCR results (0.0 - 1.0)
MIN_OCR_CONFIDENCE_THRESHOLD = 0.7

# Maximum number of retry attempts for failed operations
MAX_RETRY_ATTEMPTS = 3
