from typing import List
from pydantic import BaseModel, Field


class GradingPoint(BaseModel):
    """A specific criterion for grading a step or concept."""
    point_id: str = Field(..., description="Unique identifier for the grading point.")
    description: str = Field(..., description="Human-readable description of the required answer/step.")
    score: float = Field(..., description="Points awarded for correctly meeting this criterion.")


class TeacherRubric(BaseModel):
    """Master reference context provided by the teacher for a specific question."""
    question_id: str = Field(..., description="The unique question being graded.")
    correct_answer: str = Field(..., description="The standard model answer or solution key.")
    grading_points: List[GradingPoint] = Field(default_factory=list, description="List of granular grading criteria.")
