import logging
import asyncio
from typing import Any, Dict, Optional

import requests

from src.core.config import settings
from src.skills.interfaces import ValidationExecutionSkill, ValidationInput, ValidationResult


logger = logging.getLogger(__name__)


class HttpValidationExecutionSkill(ValidationExecutionSkill):
    """
    Generic HTTP adapter for objective validation services (e.g., E2B sandbox gateway).
    Expected response contract:
    {
      "status": "ok" | "mismatch" | "error",
      "confidence": 0.0-1.0,
      "details": {...},
      "warnings": ["..."]
    }
    """

    def __init__(self, provider: str, api_url: str, api_key: Optional[str], timeout_seconds: float) -> None:
        self._provider = provider
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "X-Skill-Provider": self._provider}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if settings.skill_gateway_auth_enabled and settings.skill_gateway_auth_token:
            headers["X-Skill-Gateway-Token"] = settings.skill_gateway_auth_token
        return headers

    async def validate(self, payload: ValidationInput) -> ValidationResult:
        request_payload = {
            "task_id": payload.task_id,
            "question_id": payload.question_id,
            "perception_payload": payload.perception_payload,
            "evaluation_payload": payload.evaluation_payload,
            "rubric_payload": payload.rubric_payload,
        }
        try:
            resp = await asyncio.to_thread(
                requests.post,
                self._api_url,
                json=request_payload,
                headers=self._headers(),
                timeout=self._timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"validation skill request failed: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"validation skill response is not valid JSON: {exc}") from exc

        status = str(data.get("status") or "error")
        if status not in {"ok", "mismatch", "error"}:
            status = "error"
        confidence = float(data.get("confidence", 0.0))
        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0

        details = data.get("details")
        if not isinstance(details, dict):
            details = {"raw": details}

        warnings = data.get("warnings")
        if not isinstance(warnings, list):
            warnings = []

        return ValidationResult(
            status=status,
            checker=self._provider,
            confidence=confidence,
            details=details,
            warnings=[str(item) for item in warnings],
        )
