import pytest

from src.core.config import settings
from src.perception.mock_engine import MockPerceptionEngine
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.perception.factory import create_perception_engine, list_supported_perception_providers


def test_factory_builds_qwen_engine(monkeypatch):
    monkeypatch.setattr(settings, "perception_provider", "qwen")
    engine = create_perception_engine()
    assert isinstance(engine, QwenVLMPerceptionEngine)


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "perception_provider", "unknown-provider")
    with pytest.raises(ValueError, match="Unsupported perception provider"):
        create_perception_engine()


def test_factory_builds_mock_engine(monkeypatch):
    monkeypatch.setattr(settings, "perception_provider", "mock")
    engine = create_perception_engine()
    assert isinstance(engine, MockPerceptionEngine)


def test_factory_lists_supported_providers():
    providers = list_supported_perception_providers()
    assert "qwen" in providers
    assert "mock" in providers
