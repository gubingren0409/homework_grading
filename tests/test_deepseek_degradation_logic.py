import json
from types import SimpleNamespace

import pytest

from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.core.config import settings
from src.schemas.perception_ir import PerceptionOutput, PerceptionNode


class _FakeKeyPool:
    def get_key_metadata(self):
        return {"key": "k1"}

    def report_429(self, key: str, cooldown_seconds: int = 60):
        return None


class _FakeStream:
    def __init__(self, chunks=None, error: Exception | None = None):
        self._chunks = chunks or []
        self._error = error
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._error is not None:
            raise self._error
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        piece = self._chunks[self._idx]
        self._idx += 1
        delta = SimpleNamespace(reasoning_content=None, content=piece)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class _SequencedCompletions:
    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        behavior = self.behaviors[idx] if idx < len(self.behaviors) else self.behaviors[-1]
        return behavior(kwargs)


def _make_engine_with_behaviors(behaviors):
    engine = DeepSeekCognitiveEngine.__new__(DeepSeekCognitiveEngine)
    engine._key_pool = _FakeKeyPool()
    completions = _SequencedCompletions(behaviors)
    engine._clients = {"k1": SimpleNamespace(chat=SimpleNamespace(completions=completions))}
    engine._system_prompt_grading_base = "system"
    return engine, completions


def _sample_perception() -> PerceptionOutput:
    return PerceptionOutput(
        readability_status="CLEAR",
        elements=[
            PerceptionNode(
                element_id="e1",
                content_type="plain_text",
                raw_content="x=1",
                confidence_score=1.0,
            )
        ],
        global_confidence=1.0,
        trigger_short_circuit=False,
    )


def _sample_perception_heavily_altered() -> PerceptionOutput:
    return PerceptionOutput(
        readability_status="HEAVILY_ALTERED",
        elements=[
            PerceptionNode(
                element_id="e1",
                content_type="plain_text",
                raw_content="illegible noisy fragment",
                confidence_score=0.4,
            )
        ],
        global_confidence=0.4,
        trigger_short_circuit=False,
    )


def _valid_eval_wrapped_json() -> str:
    payload = {
        "evaluation_report": {
            "is_fully_correct": True,
            "total_score_deduction": 0.0,
            "step_evaluations": [],
            "overall_feedback": "ok",
            "system_confidence": 0.95,
            "requires_human_review": False,
        }
    }
    return f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"


@pytest.mark.asyncio
async def test_parse_failure_triggers_v3_fallback():
    original_use_stream = settings.deepseek_use_stream
    settings.deepseek_use_stream = True

    def stream_bad_json(_kwargs):
        return _FakeStream(chunks=["```json\n", "not-json", "\n```"])

    def fallback_ok(_kwargs):
        text = _valid_eval_wrapped_json()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    try:
        engine, completions = _make_engine_with_behaviors([stream_bad_json, fallback_ok])
        report = await engine.evaluate_logic(_sample_perception())

        assert report.is_fully_correct is True
        assert len(completions.calls) == 2
        assert completions.calls[0]["stream"] is True
        assert completions.calls[1]["stream"] is False
        assert completions.calls[1]["model"] == "deepseek-chat"
    finally:
        settings.deepseek_use_stream = original_use_stream


@pytest.mark.asyncio
async def test_heavily_altered_bypasses_reasoner_to_v3():
    original_use_stream = settings.deepseek_use_stream
    settings.deepseek_use_stream = True

    def v3_ok(_kwargs):
        text = _valid_eval_wrapped_json()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    try:
        engine, completions = _make_engine_with_behaviors([v3_ok])
        report = await engine.evaluate_logic(_sample_perception_heavily_altered())

        assert report.is_fully_correct is True
        assert len(completions.calls) == 1
        assert completions.calls[0]["stream"] is False
        assert completions.calls[0]["model"] == "deepseek-chat"
    finally:
        settings.deepseek_use_stream = original_use_stream


@pytest.mark.asyncio
async def test_incomplete_chunked_read_triggers_v3_fallback():
    original_use_stream = settings.deepseek_use_stream
    settings.deepseek_use_stream = True

    err = RuntimeError("peer closed connection without sending complete message body (incomplete chunked read)")

    def stream_broken(_kwargs):
        return _FakeStream(error=err)

    def fallback_ok(_kwargs):
        text = _valid_eval_wrapped_json()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    try:
        engine, completions = _make_engine_with_behaviors([stream_broken, fallback_ok])
        report = await engine.evaluate_logic(_sample_perception())

        assert report.is_fully_correct is True
        assert len(completions.calls) == 2
        assert completions.calls[0]["stream"] is True
        assert completions.calls[1]["stream"] is False
        assert completions.calls[1]["model"] == "deepseek-chat"
    finally:
        settings.deepseek_use_stream = original_use_stream
