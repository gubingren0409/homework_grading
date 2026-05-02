from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.schemas.answer_ir import StudentAnswerBundle


class StepEvaluation(BaseModel):
    """Evaluation output for a single step of the work."""

    grading_point_id: Optional[str] = Field(
        default=None,
        description="Optional rubric grading point id when this evaluation maps to a TeacherRubric.grading_points item.",
    )
    grading_point_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Optional full score of the mapped grading point.",
    )
    awarded_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Optional score awarded for the mapped grading point.",
    )
    reference_element_id: str = Field(
        ..., description="The unique element_id from PerceptionOutput that this evaluation maps to."
    )
    is_correct: bool = Field(..., description="The correctness of the step.")
    error_type: Optional[Literal[
        "CALCULATION", "LOGIC", "CONCEPTUAL", "TRANSCRIPTION_ERROR", "NONE"
    ]] = Field(
        None, description="The structural attribution of the error, if any."
    )
    correction_suggestion: Optional[str] = Field(
        None, description="Detailed correction advice for the student."
    )


class EvaluationReport(BaseModel):
    """The final semantic snapshot output by the Cognition engine."""

    status: Literal["SCORED", "REJECTED_UNREADABLE"] = Field(
        default="SCORED", 
        description="任务状态。正常批改输出 SCORED；若输入无法阅读或逻辑完全破损则输出 REJECTED_UNREADABLE"
    )
    is_fully_correct: bool = Field(..., description="全局作答是否完全正确")
    total_score_deduction: float = Field(
        ..., ge=0.0, description="该题累计扣分 (必须为非负数)"
    )
    step_evaluations: List[StepEvaluation] = Field(
        default=[], description="针对每一具体步骤的诊断结果列表"
    )
    overall_feedback: str = Field(..., description="对作答的综合性评价文本")
    system_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="认知引擎对自身批改结论的置信度"
    )
    requires_human_review: bool = Field(
        ..., description="遇到异常解法或高熵状态时为True，强制人工介入标记"
    )


class PaperEvaluationReport(BaseModel):
    paper_id: str
    total_questions: int = Field(..., ge=0)
    answered_questions: int = Field(..., ge=0)
    total_score_deduction: float = Field(..., ge=0.0)
    requires_human_review: bool
    warnings: List[str] = Field(default_factory=list)
    per_question: Dict[str, EvaluationReport] = Field(default_factory=dict)
    student_answer_bundle: Optional[StudentAnswerBundle] = None
