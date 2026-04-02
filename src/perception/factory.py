from src.core.config import settings
from src.perception.base import BasePerceptionEngine
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine


def create_perception_engine() -> BasePerceptionEngine:
    provider = settings.perception_provider
    if provider == "qwen":
        return QwenVLMPerceptionEngine()
    raise ValueError(f"Unsupported perception provider: {provider}")
