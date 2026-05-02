from unittest.mock import Mock

import pytest
import requests

from src.skills.layout_http import HttpLayoutParserSkill
from src.skills.layout_mineru import MinerULayoutSkill
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


@pytest.mark.asyncio
async def test_mineru_layout_skill_maps_middle_json_blocks():
    skill = MinerULayoutSkill(
        api_url="http://localhost:18082",
        timeout_seconds=5.0,
    )

    submit_response = Mock()
    submit_response.raise_for_status.return_value = None
    submit_response.json.return_value = {
        "status_url": "http://localhost:18082/tasks/t-1",
        "result_url": "http://localhost:18082/tasks/t-1/result",
    }

    status_response = Mock()
    status_response.raise_for_status.return_value = None
    status_response.json.return_value = {"status": "completed"}

    result_response = Mock()
    result_response.raise_for_status.return_value = None
    result_response.json.return_value = {
        "results": {
            "page.jpg": {
                "middle_json": (
                    '{"pdf_info":[{"preproc_blocks":[{"index":1,"type":"title","bbox":[10,20,110,70],'
                    '"lines":[{"spans":[{"content":"1．试题标题"}]}]}]}]}'
                )
            }
        }
    }

    captured_post_data = {}

    def _fake_post(*args, **kwargs):
        captured_post_data["data"] = kwargs.get("data")
        return submit_response

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            requests,
            "post",
            _fake_post,
        )
        responses = iter([status_response, result_response])
        mp.setattr(
            requests,
            "get",
            lambda *args, **kwargs: next(responses),
        )
        layout = await skill.parse_layout(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00 \x00\x00\x00 \x08\x02\x00\x00\x00\xfc\x18\xed\xa3\x00\x00\x00\x16IDATx\x9cc`\xa0\x1f\x00\x00\x00D\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82",
            context_type="REFERENCE",
        )

    assert layout.regions[0].region_type == "title"
    assert layout.regions[0].question_no == "1"
    assert layout.regions[0].bbox["x_min"] == 0.3125
    assert ("formula_enable", "true") in captured_post_data["data"]
