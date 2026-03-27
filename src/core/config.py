import os
from pathlib import Path
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

    # Redis Configuration (Phase 28)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    
    # File Storage Configuration (Phase 31: Claim Check Pattern)
    uploads_dir: str = "data/uploads"  # Temporary file storage (LocalStorage)
    
    # Phase 32: Storage Adapter Configuration
    storage_backend: str = "local"  # Options: "local", "s3"
    s3_bucket: str = "homework-grading"  # S3 bucket name
    s3_endpoint_url: str | None = None  # Custom S3 endpoint (e.g., MinIO)
    aws_access_key_id: str | None = None  # S3 access key
    aws_secret_access_key: str | None = None  # S3 secret key
    
    @property
    def uploads_path(self) -> Path:
        """Absolute path to uploads directory (LocalStorage only)."""
        path = Path(self.uploads_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

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
