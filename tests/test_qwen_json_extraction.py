import json

import pytest

from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput
from src.perception.engines.qwen_engine import (
    QwenVLMPerceptionEngine,
    _is_qwen_key_access_error,
)


def _new_engine() -> QwenVLMPerceptionEngine:
    return QwenVLMPerceptionEngine()


def test_extract_json_object_candidate_handles_fenced_block():
    engine = _new_engine()
    text = "```json\n{\"a\":1,\"b\":2}\n```"
    candidate = engine._extract_json_object_candidate(text)  # type: ignore[attr-defined]
    assert candidate == '{"a":1,"b":2}'


def test_extract_json_object_candidate_handles_prefix_suffix_noise():
    engine = _new_engine()
    text = "model says: done\n{\"regions\":[],\"warnings\":[\"x\"]}\n--end--"
    candidate = engine._extract_json_object_candidate(text)  # type: ignore[attr-defined]
    assert candidate == '{"regions":[],"warnings":["x"]}'


def test_decode_json_object_requires_object_payload():
    engine = _new_engine()
    with pytest.raises(ValueError):
        engine._decode_json_object("[1,2,3]")  # type: ignore[attr-defined]


def test_decode_json_object_parses_valid_object():
    engine = _new_engine()
    payload = {"context_type": "REFERENCE", "regions": []}
    parsed = engine._decode_json_object(json.dumps(payload))  # type: ignore[attr-defined]
    assert parsed == payload


def test_qwen_access_denied_error_is_key_scoped():
    exc = Exception("Error code: 400 - Access denied, please make sure your account is in good standing.")

    assert _is_qwen_key_access_error(exc) is True
    assert _is_qwen_key_access_error(Exception("Error code: 400 - malformed request")) is False


def test_reference_output_to_dense_description_preserves_visual_and_table_content():
    output = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=1.0,
        elements=[
            PerceptionNode(
                element_id="diagram",
                content_type="circuit_schematic",
                raw_content="电源与开关串联后连接线圈 L，右侧并联可变电容 C。",
                confidence_score=0.98,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.3),
            ),
            PerceptionNode(
                element_id="table",
                content_type="table",
                raw_content="|组别|U1|U2|\n|---|---|---|\n|Ⅰ|6V|3V|",
                confidence_score=0.98,
                bbox=BoundingBox(x_min=0.1, y_min=0.4, x_max=0.8, y_max=0.6),
            ),
        ],
    )

    description = QwenVLMPerceptionEngine.reference_output_to_dense_description(output)

    assert "电源与开关串联后连接线圈 L" in description
    assert "表格转写：|组别|U1|U2|" in description
