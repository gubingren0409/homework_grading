from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.schemas.perception_ir import BoundingBox, PerceptionNode
from src.schemas.question_ir import QuestionNumber


class StudentAnswerPart(BaseModel):
    """One OCR slice contributing to a parent-question student answer."""

    source_question_no: str = Field(..., description="Original question id attached to this crop.")
    crop_index: Optional[int] = Field(default=None, description="One-based crop index in the review artifact.")
    page_index: Optional[int] = Field(default=None, ge=0, description="Zero-based source page index.")
    bbox: Optional[BoundingBox] = Field(default=None, description="Normalized crop bbox on the source page.")
    crop_path: Optional[str] = Field(default=None, description="Optional debug/review crop path.")
    text: str = Field(..., description="Cleaned OCR text for audit; may include printed question text.")
    answer_text: str = Field(default="", description="Student-written answer text extracted from OCR.")
    elements: List[PerceptionNode] = Field(default_factory=list, description="Raw perception elements.")
    global_confidence: float = Field(..., ge=0.0, le=1.0)
    readability_status: Literal["CLEAR", "MINOR_ALTERATION", "HEAVILY_ALTERED", "UNREADABLE"]
    is_blank: bool = False
    trigger_short_circuit: bool = False
    extraction_warnings: List[str] = Field(default_factory=list)
    worked_solution_block_detected: bool = Field(
        default=False,
        description="Whether this part contains a single block-level <student> tag spanning a worked-solution answer block.",
    )


class StudentAnswer(BaseModel):
    """Student answer aligned to the same parent question id used by TeacherRubric."""

    question_id: str = Field(..., description="Parent-question id aligned with RubricBundle.rubrics[].question_id.")
    stem_scope: str = Field(default="", description="Inherited parent-question context, if available.")
    answer_text: str = Field(..., description="Merged student-written answer text only.")
    ocr_text: str = Field(..., description="Merged cleaned OCR text for audit; may include printed question text.")
    parts: List[StudentAnswerPart] = Field(default_factory=list)
    answer_parts: List[StudentAnswerPart] = Field(
        default_factory=list,
        description="Stable contract alias for parts; kept explicit for downstream consumers.",
    )
    slot_answers: Dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="Explicit child-slot answers; missing slots remain null instead of shifting later answers.",
    )
    global_confidence: float = Field(..., ge=0.0, le=1.0)
    is_blank: bool = False
    readability_status: Literal["CLEAR", "MINOR_ALTERATION", "HEAVILY_ALTERED", "UNREADABLE"] = "CLEAR"
    trigger_short_circuit: bool = False
    extraction_warnings: List[str] = Field(default_factory=list)
    worked_solution_block_detected: bool = Field(
        default=False,
        description="Whether any contributing answer part used a block-level <student> tag for a worked-solution segment.",
    )


class StudentAnswerBundle(BaseModel):
    """Whole-paper student answer payload aligned with a RubricBundle."""

    paper_id: str = Field(..., description="Unique identifier for the student paper.")
    answers: List[StudentAnswer] = Field(default_factory=list)
    question_tree: List[QuestionNumber] = Field(default_factory=list)
