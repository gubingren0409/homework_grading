from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence


@dataclass(frozen=True)
class LayoutRegion:
    target_id: str
    region_type: str
    bbox: Dict[str, float]
    question_no: Optional[str] = None


@dataclass(frozen=True)
class LayoutParseResult:
    context_type: str
    page_index: int
    regions: List[LayoutRegion]
    target_question_no: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationInput:
    task_id: str
    question_id: Optional[str]
    perception_payload: Dict[str, Any]
    evaluation_payload: Dict[str, Any]
    rubric_payload: Optional[Dict[str, Any]]


@dataclass(frozen=True)
class ValidationResult:
    status: str  # ok | mismatch | error
    checker: str
    confidence: float
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationRecord:
    task_id: str
    student_id: str
    question_id: Optional[str]
    checker: str
    status: str
    confidence: float
    details_json: str


class LayoutParserSkill(Protocol):
    async def parse_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        page_index: int = 0,
        target_question_no: Optional[str] = None,
    ) -> LayoutParseResult: ...


class ValidationExecutionSkill(Protocol):
    async def validate(self, payload: ValidationInput) -> ValidationResult: ...


class ValidationSinkSkill(Protocol):
    async def persist(self, records: Sequence[ValidationRecord]) -> int: ...
