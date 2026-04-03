from typing import Optional

from src.skills.interfaces import (
    LayoutParseResult,
    ValidationInput,
    ValidationResult,
    ValidationSinkSkill,
    ValidationRecord,
)


class NoopLayoutParserSkill:
    async def parse_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        page_index: int = 0,
        target_question_no: Optional[str] = None,
    ) -> LayoutParseResult:
        del image_bytes, target_question_no
        return LayoutParseResult(
            context_type=context_type,
            page_index=page_index,
            regions=[],
            target_question_no=target_question_no,
            warnings=["layout skill disabled"],
        )


class NoopValidationExecutionSkill:
    async def validate(self, payload: ValidationInput) -> ValidationResult:
        del payload
        return ValidationResult(
            status="ok",
            checker="noop",
            confidence=0.0,
            warnings=["validation skill disabled"],
        )


class NoopValidationSinkSkill:
    async def persist(self, records: list[ValidationRecord]) -> int:
        return len(records)
