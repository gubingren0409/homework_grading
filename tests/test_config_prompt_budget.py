import pytest

from src.core.config import Settings


def test_prompt_budget_defaults_relaxed():
    cfg = Settings()
    assert cfg.prompt_max_input_tokens == 32768
    assert cfg.prompt_reserve_output_tokens == 1024
    assert cfg.prompt_max_input_tokens - cfg.prompt_reserve_output_tokens == 31744


def test_sse_defaults():
    cfg = Settings()
    assert cfg.sse_stream_timeout_seconds == 1800
    assert cfg.sse_heartbeat_interval_seconds == 3.0


def test_sse_heartbeat_must_be_positive():
    with pytest.raises(ValueError, match="sse_heartbeat_interval_seconds"):
        Settings(sse_heartbeat_interval_seconds=0)

