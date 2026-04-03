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
    perception_provider: str = "qwen"

    # DeepSeek Configuration
    deepseek_api_key: str | None = None # Legacy support
    deepseek_api_keys: str | None = None # Comma-separated keys
    deepseek_model_name: str = "deepseek-reasoner"
    deepseek_use_stream: bool = False

    # Redis Configuration (Phase 28)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    celery_task_always_eager: bool = False

    # Database configuration
    sqlite_db_path: str = "outputs/grading_database.db"
    
    # File Storage Configuration (Phase 31: Claim Check Pattern)
    uploads_dir: str = "data/uploads"  # Temporary file storage (LocalStorage)
    
    # Phase 32: Storage Adapter Configuration
    storage_backend: str = "local"  # Options: "local", "s3"
    s3_bucket: str = "homework-grading"  # S3 bucket name
    s3_endpoint_url: str | None = None  # Custom S3 endpoint (e.g., MinIO)
    aws_access_key_id: str | None = None  # S3 access key
    aws_secret_access_key: str | None = None  # S3 secret key

    # Phase 35: Layout preprocessing switch (must stay enabled to enforce spatial contract)
    enable_layout_preprocess: bool = True

    # HTTP ingress hard limits (E05)
    max_request_body_bytes: int = 20 * 1024 * 1024  # 20MB hard cap
    request_body_read_timeout_seconds: float = 5.0
    upload_chunk_size_bytes: int = 256 * 1024
    upload_spool_max_size_bytes: int = 1 * 1024 * 1024

    # Prompt provider foundation (Phase 41)
    prompts_dir: str = "configs/prompts"
    prompt_l1_ttl_seconds: int = 120
    prompt_l1_swr_seconds: int = 30
    prompt_l2_ttl_seconds: int = 1800
    prompt_pull_interval_seconds: int = 30
    prompt_l2_key_prefix: str = "prompt:l2:"
    prompt_invalidation_channel: str = "prompt:invalidate"
    prompt_invalidation_bus_enabled: bool = True

    # Optional external skills (Phase 43)
    skill_layout_parser_enabled: bool = False
    skill_layout_parser_provider: str = "none"  # none | llamaparse | unstructured
    skill_layout_parser_api_url: str | None = None
    skill_layout_parser_api_key: str | None = None
    skill_layout_parser_timeout_seconds: float = 20.0

    skill_validation_enabled: bool = False
    skill_validation_provider: str = "none"  # none | e2b
    skill_validation_api_url: str | None = None
    skill_validation_api_key: str | None = None
    skill_validation_timeout_seconds: float = 20.0
    skill_validation_fail_open: bool = True
    
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

    @field_validator("perception_provider")
    @classmethod
    def _normalize_perception_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise ValueError("perception_provider must be a non-empty string")
        return normalized

    @field_validator("skill_layout_parser_provider", "skill_validation_provider")
    @classmethod
    def _normalize_skill_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            raise ValueError("skill provider must be a non-empty string")
        return normalized

    @field_validator("skill_layout_parser_timeout_seconds", "skill_validation_timeout_seconds")
    @classmethod
    def _validate_skill_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("skill timeout must be positive")
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Instantiate the global settings object
settings = Settings()
