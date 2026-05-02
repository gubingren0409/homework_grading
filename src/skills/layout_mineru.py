import asyncio
import json
import re
import time
from io import BytesIO
from typing import Any, Dict, List, Optional

import requests
from PIL import Image

from src.skills.interfaces import LayoutParseResult, LayoutParserSkill, LayoutRegion


def _map_mineru_http_error(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.Timeout):
        return "SKILL_LAYOUT_TIMEOUT"
    if isinstance(exc, requests.ConnectionError):
        return "SKILL_LAYOUT_UNAVAILABLE"
    if isinstance(exc, requests.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in {401, 403}:
            return "SKILL_LAYOUT_UNAUTHORIZED"
        if status_code == 429:
            return "SKILL_LAYOUT_RATE_LIMITED"
        if status_code is not None and status_code >= 500:
            return "SKILL_LAYOUT_UPSTREAM_ERROR"
        return "SKILL_LAYOUT_BAD_REQUEST"
    return "SKILL_LAYOUT_REQUEST_FAILED"


class MinerULayoutSkill(LayoutParserSkill):
    def __init__(self, api_url: str, timeout_seconds: float) -> None:
        self._api_url = api_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def parse_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        page_index: int = 0,
        target_question_no: Optional[str] = None,
    ) -> LayoutParseResult:
        width, height = self._image_dimensions(image_bytes)
        task_payload = [
            ("backend", "pipeline"),
            ("parse_method", "txt" if context_type == "REFERENCE" else "auto"),
            ("lang_list", "ch"),
            ("formula_enable", "true" if context_type == "REFERENCE" else "false"),
            ("table_enable", "false"),
            ("return_md", "false"),
            ("return_middle_json", "true"),
        ]

        try:
            submit_response = await asyncio.to_thread(
                requests.post,
                f"{self._api_url}/tasks",
                files={"files": (f"page-{page_index}.jpg", image_bytes, "image/jpeg")},
                data=task_payload,
                timeout=self._timeout_seconds,
            )
            submit_response.raise_for_status()
            task_data = submit_response.json()
        except requests.RequestException as exc:
            raise RuntimeError(_map_mineru_http_error(exc)) from exc
        except ValueError as exc:
            raise RuntimeError("SKILL_LAYOUT_INVALID_RESPONSE") from exc

        status_url = task_data.get("status_url")
        result_url = task_data.get("result_url")
        if not status_url or not result_url:
            raise RuntimeError("SKILL_LAYOUT_INVALID_RESPONSE")

        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            try:
                status_response = await asyncio.to_thread(
                    requests.get,
                    status_url,
                    timeout=min(30.0, self._timeout_seconds),
                )
                status_response.raise_for_status()
                status_data = status_response.json()
            except requests.RequestException as exc:
                raise RuntimeError(_map_mineru_http_error(exc)) from exc
            except ValueError as exc:
                raise RuntimeError("SKILL_LAYOUT_INVALID_RESPONSE") from exc

            status = str(status_data.get("status") or "").lower()
            if status == "completed":
                break
            if status == "failed":
                detail = status_data.get("error") or "MinerU parse failed"
                raise RuntimeError(f"SKILL_LAYOUT_UPSTREAM_ERROR: {detail}")
            await asyncio.sleep(1.0)
        else:
            raise RuntimeError("SKILL_LAYOUT_TIMEOUT")

        try:
            result_response = await asyncio.to_thread(
                requests.get,
                result_url,
                timeout=min(60.0, self._timeout_seconds),
            )
            result_response.raise_for_status()
            result_data = result_response.json()
        except requests.RequestException as exc:
            raise RuntimeError(_map_mineru_http_error(exc)) from exc
        except ValueError as exc:
            raise RuntimeError("SKILL_LAYOUT_INVALID_RESPONSE") from exc

        regions, warnings = self._parse_regions(
            result_data,
            image_width=width,
            image_height=height,
        )
        return LayoutParseResult(
            context_type=context_type,
            page_index=page_index,
            regions=regions,
            target_question_no=target_question_no,
            warnings=warnings,
        )

    def _image_dimensions(self, image_bytes: bytes) -> tuple[int, int]:
        with Image.open(BytesIO(image_bytes)) as image:
            return image.size

    def _parse_regions(
        self,
        result_data: Dict[str, Any],
        *,
        image_width: int,
        image_height: int,
    ) -> tuple[List[LayoutRegion], List[str]]:
        results = result_data.get("results") or {}
        if not isinstance(results, dict) or not results:
            return [], ["mineru result is empty"]

        document_result = next(iter(results.values()))
        if not isinstance(document_result, dict):
            return [], ["mineru document result is invalid"]

        middle_json_raw = document_result.get("middle_json")
        if not middle_json_raw:
            return [], ["mineru middle_json is missing"]

        try:
            middle_json = json.loads(middle_json_raw)
        except (TypeError, json.JSONDecodeError):
            return [], ["mineru middle_json is invalid"]

        pdf_info = middle_json.get("pdf_info") or []
        if not pdf_info:
            return [], ["mineru pdf_info is empty"]

        preproc_blocks = pdf_info[0].get("preproc_blocks") or []
        regions: List[LayoutRegion] = []
        for block in preproc_blocks:
            bbox = block.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = [float(value) for value in bbox]
            block_type = str(block.get("type") or "text")
            target_id = f"block_{block.get('index', len(regions) + 1)}"
            block_text = self._extract_block_text(block)
            regions.append(
                LayoutRegion(
                    target_id=target_id,
                    region_type=block_type,
                    bbox={
                        "x_min": max(0.0, min(1.0, x1 / image_width)),
                        "y_min": max(0.0, min(1.0, y1 / image_height)),
                        "x_max": max(0.0, min(1.0, x2 / image_width)),
                        "y_max": max(0.0, min(1.0, y2 / image_height)),
                    },
                    question_no=self._extract_question_no(block_text),
                )
            )
        return regions, []

    def _extract_block_text(self, block: Dict[str, Any]) -> str:
        parts: List[str] = []
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                content = span.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(content.strip())
        return " ".join(parts)

    def _extract_question_no(self, text: str) -> Optional[str]:
        if not text:
            return None
        stripped = text.strip()
        patterns = [
            (r"^([一二三四五六七八九十百千]+)、", lambda m: m.group(1)),
            (r"^(?:第\s*(\d+)\s*题|(\d+)[\.．、])", lambda m: m.group(1) or m.group(2)),
            (r"^[（(](\d+)[)）]", lambda m: f"({m.group(1)})"),
            (r"^([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])", lambda m: m.group(1)),
        ]
        for pattern, transform in patterns:
            match = re.match(pattern, stripped)
            if match:
                return transform(match)
        return None
