import pytest

from src.core.config import settings
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.perception.factory import create_perception_engine


def test_factory_builds_qwen_engine(monkeypatch):
    monkeypatch.setattr(settings, "perception_provider", "qwen")
    engine = create_perception_engine()
    assert isinstance(engine, QwenVLMPerceptionEngine)


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "perception_provider", "unknown-provider")
    with pytest.raises(ValueError, match="Unsupported perception provider"):
        create_perception_engine()
