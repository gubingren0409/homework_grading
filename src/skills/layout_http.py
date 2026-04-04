import asyncio
import logging
import base64
from typing import Dict, List, Optional

import requests

from src.core.config import settings
from src.skills.interfaces import LayoutParseResult, LayoutParserSkill, LayoutRegion


logger = logging.getLogger(__name__)


class HttpLayoutParserSkill(LayoutParserSkill):
    """
    Generic HTTP adapter for external layout parsing services (e.g., LlamaParse/Unstructured proxy).
    Expected response (minimal contract):
    {
      "context_type": "...",
      "page_index": 0,
      "regions": [{"target_id":"...", "region_type":"answer_region", "bbox":{"x_min":0.1,...}}],
      "warnings": []
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

    async def parse_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        page_index: int = 0,
        target_question_no: Optional[str] = None,
    ) -> LayoutParseResult:
        encoded_image = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "context_type": context_type,
            "page_index": page_index,
            "target_question_no": target_question_no,
            "image_base64": encoded_image,
        }
        try:
            resp = await asyncio.to_thread(
                requests.post,
                self._api_url,
                json=payload,
                headers=self._headers(),
                timeout=self._timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"layout parser request failed: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"layout parser response is not valid JSON: {exc}") from exc
        regions: List[LayoutRegion] = []
        for item in data.get("regions", []):
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox", {})
            if not isinstance(bbox, dict):
                continue
            try:
                region = LayoutRegion(
                    target_id=str(item.get("target_id") or ""),
                    region_type=str(item.get("region_type") or "answer_region"),
                    bbox={
                        "x_min": float(bbox["x_min"]),
                        "y_min": float(bbox["y_min"]),
                        "x_max": float(bbox["x_max"]),
                        "y_max": float(bbox["y_max"]),
                    },
                    question_no=item.get("question_no"),
                )
            except Exception:
                continue
            if region.target_id:
                regions.append(region)

        return LayoutParseResult(
            context_type=str(data.get("context_type") or context_type),
            page_index=int(data.get("page_index", page_index)),
            regions=regions,
            target_question_no=data.get("target_question_no"),
            warnings=[str(x) for x in data.get("warnings", []) if isinstance(x, (str, int, float))],
        )


def build_layout_parser_from_settings() -> Optional[LayoutParserSkill]:
    if not settings.skill_layout_parser_enabled:
        return None
    provider = settings.skill_layout_parser_provider
    if provider not in {"llamaparse", "unstructured"}:
        logger.warning("layout parser provider is not supported: %s", provider)
        return None
    if not settings.skill_layout_parser_api_url:
        logger.warning("layout parser enabled but api url is missing")
        return None
    return HttpLayoutParserSkill(
        provider=provider,
        api_url=settings.skill_layout_parser_api_url,
        api_key=settings.skill_layout_parser_api_key,
        timeout_seconds=settings.skill_layout_parser_timeout_seconds,
    )
