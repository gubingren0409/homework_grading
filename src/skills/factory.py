import logging
from dataclasses import dataclass
from typing import Optional

from src.core.config import settings
from src.skills.db_sink import DbValidationSinkSkill
from src.skills.interfaces import LayoutParserSkill, ValidationExecutionSkill, ValidationSinkSkill
from src.skills.layout_http import build_layout_parser_from_settings
from src.skills.validation_http import HttpValidationExecutionSkill


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillBundle:
    layout_parser: Optional[LayoutParserSkill]
    validation_executor: Optional[ValidationExecutionSkill]
    validation_sink: ValidationSinkSkill


def build_validation_executor_from_settings() -> Optional[ValidationExecutionSkill]:
    if not settings.skill_validation_enabled:
        return None

    provider = settings.skill_validation_provider
    if provider != "e2b":
        logger.warning("validation provider is not supported: %s", provider)
        return None
    if not settings.skill_validation_api_url:
        logger.warning("validation skill enabled but api url is missing")
        return None
    return HttpValidationExecutionSkill(
        provider=provider,
        api_url=settings.skill_validation_api_url,
        api_key=settings.skill_validation_api_key,
        timeout_seconds=settings.skill_validation_timeout_seconds,
    )


def build_skill_bundle(*, db_path: Optional[str] = None) -> SkillBundle:
    if not db_path:
        db_path = settings.sqlite_db_path
    return SkillBundle(
        layout_parser=build_layout_parser_from_settings(),
        validation_executor=build_validation_executor_from_settings(),
        validation_sink=DbValidationSinkSkill(db_path=db_path),
    )
