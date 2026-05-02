from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class QuestionNumber(BaseModel):
    """Normalized question-number tree node extracted from a whole paper."""

    raw_label: str = Field(..., description="Original printed label detected on the paper.")
    normalized_path: List[str] = Field(
        default_factory=list,
        description='Normalized hierarchical path, e.g. ["一", "1", "(1)"].',
    )
    order_index: int = Field(..., ge=0, description="Zero-based reading-order index in the paper.")
    children: List["QuestionNumber"] = Field(
        default_factory=list,
        description="Nested sub-questions under the current question node.",
    )


QuestionNumber.model_rebuild()
