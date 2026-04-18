from unittest.mock import Mock

import pytest
import requests

from src.skills.layout_http import HttpLayoutParserSkill
from src.skills.validation_http import HttpValidationExecutionSkill
from src.skills.interfaces import ValidationInput


@pytest.mark.asyncio
async def test_layout_http_adapter_maps_timeout():
    skill = HttpLayoutParserSkill(
        provider="llamaparse",
        api_url="http://localhost:18080/layout",
        api_key=None,
        timeout_seconds=1.0,
    )

    with pytest.raises(RuntimeError, match="SKILL_LAYOUT_TIMEOUT"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                requests,
                "post",
                lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("timeout")),
            )
            await skill.parse_layout(b"img", context_type="STUDENT_ANSWER")


@pytest.mark.asyncio
async def test_validation_http_adapter_maps_unauthorized():
    skill = HttpValidationExecutionSkill(
        provider="e2b",
        api_url="http://localhost:18081/validate",
        api_key=None,
        timeout_seconds=1.0,
    )

    response = Mock()
    response.status_code = 401
    http_error = requests.HTTPError("unauthorized", response=response)

    with pytest.raises(RuntimeError, match="SKILL_VALIDATION_UNAUTHORIZED"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                requests,
                "post",
                lambda *args, **kwargs: (_ for _ in ()).throw(http_error),
            )
            await skill.validate(
                ValidationInput(
                    task_id="t1",
                    question_id="q1",
                    perception_payload={},
                    evaluation_payload={},
                    rubric_payload={},
                )
            )
