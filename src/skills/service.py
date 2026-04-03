import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.core.config import settings
from src.skills.factory import build_skill_bundle
from src.skills.interfaces import LayoutParseResult, ValidationInput, ValidationRecord, ValidationResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillValidationOutcome:
    applied: bool
    result: Optional[ValidationResult]
    error: Optional[str] = None


class SkillService:
    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._bundle = build_skill_bundle(db_path=db_path)

    async def try_parse_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        page_index: int = 0,
        target_question_no: Optional[str] = None,
    ) -> Optional[LayoutParseResult]:
        parser = self._bundle.layout_parser
        if parser is None:
            return None
        try:
            result = await parser.parse_layout(
                image_bytes,
                context_type=context_type,
                page_index=page_index,
                target_question_no=target_question_no,
            )
            if not result.regions:
                return None
            return result
        except Exception as exc:
            logger.warning("layout skill failed, fallback to engine layout: %s", exc)
            return None

    async def run_validation(
        self,
        *,
        task_id: str,
        student_id: str,
        question_id: Optional[str],
        perception_payload: Dict[str, Any],
        evaluation_payload: Dict[str, Any],
        rubric_payload: Optional[Dict[str, Any]],
    ) -> SkillValidationOutcome:
        executor = self._bundle.validation_executor
        if executor is None:
            return SkillValidationOutcome(applied=False, result=None)

        try:
            result = await executor.validate(
                ValidationInput(
                    task_id=task_id,
                    question_id=question_id,
                    perception_payload=perception_payload,
                    evaluation_payload=evaluation_payload,
                    rubric_payload=rubric_payload,
                )
            )
        except Exception as exc:
            msg = str(exc)
            if settings.skill_validation_fail_open:
                logger.warning("validation skill failed-open: %s", msg)
                return SkillValidationOutcome(applied=False, result=None, error=msg)
            raise

        record = ValidationRecord(
            task_id=task_id,
            student_id=student_id,
            question_id=question_id,
            checker=result.checker,
            status=result.status,
            confidence=result.confidence,
            details_json=json.dumps(
                {"details": result.details, "warnings": result.warnings},
                ensure_ascii=False,
            ),
        )
        await self._bundle.validation_sink.persist([record])
        return SkillValidationOutcome(applied=True, result=result)
