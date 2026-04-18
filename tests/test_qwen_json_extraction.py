import json

import pytest

from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine


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
