import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    """
    Global application settings managed via environment variables.
    Supports API Key pooling for high-concurrency (Phase 22).
    """
    # Qwen-VL Configuration
    qwen_api_key: str | None = None  # Legacy support
    qwen_api_keys: str | None = None # Comma-separated keys
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model_name: str = "qwen-vl-max"

    # DeepSeek Configuration
    deepseek_api_key: str | None = None # Legacy support
    deepseek_api_keys: str | None = None # Comma-separated keys
    deepseek_model_name: str = "deepseek-reasoner"
    deepseek_use_stream: bool = False

    # Internal Parsed Lists (Computed)
    @property
    def parsed_deepseek_keys(self) -> List[str]:
        # Priority: deepseek_api_keys > deepseek_api_key
        source = self.deepseek_api_keys or self.deepseek_api_key
        if not source:
            return []
        return [k.strip() for k in source.split(",") if k.strip()]

    @property
    def parsed_qwen_keys(self) -> List[str]:
        # Priority: qwen_api_keys > qwen_api_key
        source = self.qwen_api_keys or self.qwen_api_key
        if not source:
            return []
        return [k.strip() for k in source.split(",") if k.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Instantiate the global settings object
settings = Settings()
