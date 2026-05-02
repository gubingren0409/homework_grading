from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.schemas.question_ir import QuestionNumber


class GradingPoint(BaseModel):
    """A specific criterion for grading a step or concept."""
    point_id: str = Field(..., description="Unique identifier for the grading point.")
    description: str = Field(..., description="Human-readable description of the required answer/step.")
    score: float = Field(..., description="Points awarded for correctly meeting this criterion.")
    scope: Optional[str] = Field(
        default=None,
        description="Optional parent or child slot this point belongs to, e.g. 'parent' or '(1)'.",
    )


class ReferenceEvidencePart(BaseModel):
    """One OCR/perception evidence chunk contributing to a parent-question reference unit."""

    source_question_no: str = Field(..., description="Original question id attached to this reference evidence.")
    text: str = Field(..., description="Normalized OCR/reference text for this evidence part.")
    global_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RubricVisualEvidence(BaseModel):
    """Visual evidence retained from a printed reference answer."""

    evidence_id: str = Field(..., description="Stable identifier for the visual evidence.")
    evidence_type: Literal[
        "image_diagram",
        "table",
        "image",
        "coordinate_plot",
        "circuit_schematic",
        "geometry_topology",
        "image_asset",
    ] = Field(..., description="Visual evidence category.")
    description: Optional[str] = Field(
        default=None,
        description="Dense textual description/transcription available to the cognitive layer.",
    )
    asset_ref: Optional[str] = Field(
        default=None,
        description="Optional image/table asset reference for review UI or future multimodal grading.",
    )
    source_element_id: Optional[str] = Field(
        default=None,
        description="Original perception element id when available.",
    )


class TeacherRubric(BaseModel):
    """Master reference context provided by the teacher for a specific question."""
    question_id: str = Field(
        ...,
        description="The normalized question identifier/path for the graded question.",
    )
    correct_answer: str = Field(..., description="The standard model answer or solution key.")
    grading_points: List[GradingPoint] = Field(default_factory=list, description="List of granular grading criteria.")
    visual_evidence: List[RubricVisualEvidence] = Field(
        default_factory=list,
        description="Printed-reference visual evidence preserved alongside the text rubric.",
    )
    context_stem_text: str = Field(
        default="",
        description="Shared parent-question stem/context before child slots.",
    )
    subquestions: List[str] = Field(
        default_factory=list,
        description="Ordered child slots inside this parent-question unit, e.g. ['(1)', '(2)'].",
    )
    reference_evidence_parts: List[ReferenceEvidencePart] = Field(
        default_factory=list,
        description="Stable ordered OCR/reference chunks used to build this parent-question rubric.",
    )
    solution_slots: Dict[str, str] = Field(
        default_factory=dict,
        description="Normalized solution text keyed by child slot; parent-level content may use 'parent'.",
    )


class RubricBundle(BaseModel):
    """Whole-paper rubric payload containing per-question rubrics and the question tree."""

    paper_id: str = Field(..., description="Unique identifier for the reference paper.")
    rubrics: List[TeacherRubric] = Field(
        default_factory=list,
        description="Per-question rubrics extracted from the whole reference paper.",
    )
    question_tree: List[QuestionNumber] = Field(
        default_factory=list,
        description="Hierarchical question-number tree for the reference paper.",
    )
