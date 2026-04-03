from src.core.config import settings
from src.perception.base import BasePerceptionEngine
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.perception.mock_engine import MockPerceptionEngine


SUPPORTED_PERCEPTION_PROVIDERS = ("qwen", "mock")


def list_supported_perception_providers() -> list[str]:
    return list(SUPPORTED_PERCEPTION_PROVIDERS)


def create_perception_engine() -> BasePerceptionEngine:
    provider = str(settings.perception_provider).strip().lower()
    if provider == "qwen":
        return QwenVLMPerceptionEngine()
    if provider == "mock":
        return MockPerceptionEngine()
    raise ValueError(f"Unsupported perception provider: {provider}")
