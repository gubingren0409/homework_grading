import base64
import logging
import re
import json
import asyncio
import itertools
from typing import Any, Dict, Optional
from io import BytesIO

import openai
from PIL import Image
from src.core.config import settings
from src.core.connection_pool import CircuitBreakerKeyPool, AllKeysExhaustedError
from src.core.exceptions import GradingSystemError
from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError  # Phase 33
from src.core.trace_context import outbound_trace_headers
from src.perception.base import BasePerceptionEngine
from src.schemas.perception_ir import PerceptionOutput, LayoutIR
from src.perception.prompts import QWEN_PERCEPTION_SYSTEM_PROMPT, LAYOUT_ANALYSIS_PROMPT


logger = logging.getLogger(__name__)


class QwenVLMPerceptionEngine(BasePerceptionEngine):
    """
    Implementation of the Visual Perception Engine using Qwen-VL.
    Supports API Key pooling and dynamic circuit breaker (Phase 22.5).
    Phase 33: Global circuit breaker for API service-level protection.
    """

    def __init__(self):
        """Initialize the pool of circuit-breaker-aware clients for Qwen-VL."""
        keys = settings.parsed_qwen_keys or ["MISSING"]
        self._key_pool = CircuitBreakerKeyPool("QwenPool", keys)
        
        self._clients = {
            key: openai.AsyncOpenAI(
                api_key=key,
                base_url=settings.qwen_base_url,
                timeout=300.0,
                max_retries=0 # Manual failover
            ) for key in keys
        }

        # Use centralized system prompt from prompts.py
        self._system_prompt = QWEN_PERCEPTION_SYSTEM_PROMPT
        # Throttling: Max 3 concurrent physical connections to Qwen API
        self._api_semaphore = asyncio.Semaphore(3)
        
        # Phase 33: Global circuit breaker for Qwen API service
        self._circuit_breaker = CircuitBreaker(
            name="qwen_api_service",
            failure_threshold=5,        # Open after 5 consecutive failures
            recovery_timeout=60.0,      # Wait 60s before attempting recovery
            success_threshold=2,         # Need 2 successes to fully recover
            expected_exceptions=(
                openai.APIError,
                openai.APIConnectionError,
                openai.RateLimitError,
                openai.InternalServerError,
            ),
        )

    def _encode_image(self, image_bytes: bytes) -> str:
        """Converts raw image bytes to a base64-encoded string."""
        return base64.b64encode(image_bytes).decode("utf-8")

    def _clean_json_text(self, text: str) -> str:
        """Removes potential Markdown code block delimiters from the response."""
        pattern = r"```(?:json)?\s*(.*?)\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _sanitize_coordinates(self, data: dict) -> dict:
        """Intercepts and clips VLM hallucinated coordinates."""
        if "elements" not in data or not isinstance(data["elements"], list):
            return data
            
        for elem in data["elements"]:
            if "bbox" in elem and elem["bbox"]:
                if isinstance(elem["bbox"], list):
                    elem["bbox"] = [max(0.0, min(1.0, float(c))) for c in elem["bbox"]]
                elif isinstance(elem["bbox"], dict):
                    for k, v in elem["bbox"].items():
                        if isinstance(v, (int, float)):
                            elem["bbox"][k] = max(0.0, min(1.0, float(v)))
        return data

    def _image_dimensions(self, image_bytes: bytes) -> tuple[int, int]:
        with Image.open(BytesIO(image_bytes)) as img:
            return img.size  # (width, height)

    def _sanitize_layout_coordinates(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 35:
        Convert VLM bbox [ymin, xmin, ymax, xmax] in [0,1000] to normalized [0,1]
        and mapped dict {"y_min","x_min","y_max","x_max"} for LayoutIR->BoundingBox.
        """
        regions = data.get("regions")
        if not isinstance(regions, list):
            data["regions"] = []
            return data

        sanitized_regions = []
        for idx, region in enumerate(regions):
            if not isinstance(region, dict):
                continue
            raw_bbox = region.get("bbox")
            if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
                continue

            try:
                ymin, xmin, ymax, xmax = [float(v) for v in raw_bbox]
            except Exception:
                continue

            # clamp to [0,1000]
            ymin = max(0.0, min(1000.0, ymin))
            xmin = max(0.0, min(1000.0, xmin))
            ymax = max(0.0, min(1000.0, ymax))
            xmax = max(0.0, min(1000.0, xmax))

            # ensure monotonic box
            if ymax < ymin:
                ymin, ymax = ymax, ymin
            if xmax < xmin:
                xmin, xmax = xmax, xmin

            sanitized_region = dict(region)
            sanitized_region["target_id"] = str(region.get("target_id") or f"region_{idx}")
            sanitized_region["bbox"] = {
                "x_min": xmin / 1000.0,
                "y_min": ymin / 1000.0,
                "x_max": xmax / 1000.0,
                "y_max": ymax / 1000.0,
            }
            sanitized_regions.append(sanitized_region)

        data["regions"] = sanitized_regions
        return data

    async def _call_qwen_json(
        self,
        *,
        base64_image: str,
        system_prompt: str,
        user_text: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Shared low-level JSON call for Phase 35 dual-mode perception.
        """
        connection_error_count = 0
        max_retries = 10
        max_connection_errors = 5

        for attempt in range(max_retries + 1):
            try:
                key_meta = self._key_pool.get_key_metadata()
                current_key = key_meta["key"]
                client = self._clients[current_key]

                async with self._api_semaphore:
                    logger.info(
                        "llm_request_outbound",
                        extra={"extra_fields": {"component": "qwen-engine", "model": settings.qwen_model_name, "mode": "json"}}
                    )

                    @self._circuit_breaker
                    async def _protected_call():
                        return await client.chat.completions.create(
                            model=settings.qwen_model_name,
                            extra_headers=outbound_trace_headers(),
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": user_text},
                                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                                    ],
                                },
                            ],
                            temperature=temperature,
                            max_tokens=max_tokens,
                            response_format={"type": "json_object"},
                        )

                    response = await _protected_call()
                    connection_error_count = 0

                    raw = response.choices[0].message.content
                    if not raw:
                        raise GradingSystemError("Received empty response from Qwen-VL.")
                    cleaned = self._clean_json_text(raw)
                    return json.loads(cleaned)

            except AllKeysExhaustedError:
                raise
            except CircuitBreakerOpenError:
                raise
            except openai.RateLimitError:
                connection_error_count = 0
                self._key_pool.report_429(current_key)  # type: ignore[name-defined]
                await asyncio.sleep(0.5)
                continue
            except (openai.APIConnectionError, openai.APITimeoutError) as net_err:
                connection_error_count += 1
                if connection_error_count > max_connection_errors:
                    raise GradingSystemError(f"Persistent network instability for Qwen: {str(net_err)}")
                await asyncio.sleep(2.0)
                continue
            except json.JSONDecodeError as dec_err:
                raise GradingSystemError(f"VLM JSON decode failed: {dec_err}")
            except Exception as e:
                raise GradingSystemError(f"An unexpected error occurred during perception: {str(e)}")

        raise GradingSystemError("Qwen-VL request exhausted retries.")

    async def extract_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        target_question_no: Optional[str] = None,
        page_index: int = 0,
    ) -> LayoutIR:
        """
        Phase 35: Layout Analysis Agent mode using existing Qwen-VL backend.
        """
        width, height = self._image_dimensions(image_bytes)
        base64_image = self._encode_image(image_bytes)
        user_text = (
            f"context_type={context_type}\n"
            f"target_question_no={target_question_no if target_question_no else 'AUTO'}\n"
            "Return only JSON following the schema."
        )

        raw = await self._call_qwen_json(
            base64_image=base64_image,
            system_prompt=LAYOUT_ANALYSIS_PROMPT,
            user_text=user_text,
            max_tokens=2048,
            temperature=0.0,
        )
        raw.setdefault("context_type", context_type)
        raw.setdefault("target_question_no", target_question_no)
        raw.setdefault("page_index", page_index)
        raw.setdefault("warnings", [])
        sanitized = self._sanitize_layout_coordinates(raw)
        return LayoutIR.model_validate(
            sanitized,
            context={"image_width": width, "image_height": height},
        )

    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        """
        Asynchronously processes raw image bytes using Qwen-VL with Circuit-Breaker pooling (Phase 22.6).
        """
        from src.core.connection_pool import AllKeysExhaustedError
        base64_image = self._encode_image(image_bytes)
        
        # Throttling and retry logic
        max_retries = 10 
        connection_error_count = 0
        MAX_CONNECTION_ERRORS = 5
        base_delay = 1.0
        
        for attempt in range(max_retries + 1):
            try:
                # 1. Get healthy key
                key_meta = self._key_pool.get_key_metadata()
                current_key = key_meta["key"]
                client = self._clients[current_key]

                async with self._api_semaphore:
                    logger.info(f"Initiating VLM request to {settings.qwen_model_name} (Attempt {attempt + 1})...")
                    logger.info(
                        "llm_request_outbound",
                        extra={"extra_fields": {"component": "qwen-engine", "model": settings.qwen_model_name}},
                    )
                    
                    # Phase 33: Wrap API call with circuit breaker
                    @self._circuit_breaker
                    async def _protected_api_call():
                        return await client.chat.completions.create(
                            model=settings.qwen_model_name,
                            extra_headers=outbound_trace_headers(),
                            messages=[
                                {"role": "system", "content": self._system_prompt},
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": "Extract all structural elements from this homework image."},
                                        {
                                            "type": "image_url",
                                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                                        }
                                    ]
                                }
                            ],
                            temperature=0.01,
                            response_format={"type": "json_object"}
                        )
                    
                    response = await _protected_api_call()
                    
                    # Success: Reset network failure counter
                    connection_error_count = 0
                    
                    raw_response_text = response.choices[0].message.content
                    if not raw_response_text:
                        raise GradingSystemError("Received empty response from Qwen-VL.")

                    cleaned_text = self._clean_json_text(raw_response_text)
                    
                    try:
                        parsed_dict = json.loads(cleaned_text)
                        sanitized_dict = self._sanitize_coordinates(parsed_dict)
                        
                        return PerceptionOutput.model_validate(sanitized_dict)
                        
                    except Exception as ve:
                        logger.error(f"Schema validation failed: {ve}\nRaw Output: {cleaned_text}")
                        raise GradingSystemError(f"VLM output failed schema validation: {str(ve)}")

            except AllKeysExhaustedError as e:
                logger.error(f"FATAL: {e}")
                raise GradingSystemError("All Qwen-VL API keys are rate-limited. System saturated.")
            
            except CircuitBreakerOpenError as e:
                # Phase 33: Circuit breaker OPEN, service degraded
                logger.error(f"Qwen API circuit breaker OPEN: {e}")
                raise GradingSystemError(
                    f"Qwen API service degraded. Circuit breaker active. {str(e)}"
                )

            except openai.RateLimitError:
                # 2. CIRCUIT BREAKER FAILOVER
                connection_error_count = 0
                logger.warning(f"Rate limit hit on Qwen Key. Tripping circuit breaker... (Attempt {attempt+1})")
                self._key_pool.report_429(current_key)
                await asyncio.sleep(0.5) 
                continue

            except (openai.APIConnectionError, openai.APITimeoutError) as net_err:
                connection_error_count += 1
                if connection_error_count > MAX_CONNECTION_ERRORS:
                    logger.error(f"Global network failure detected for Qwen (Errors: {connection_error_count}). Aborting.")
                    raise GradingSystemError(f"Persistent network instability for Qwen: {str(net_err)}")
                
                logger.warning(f"Qwen Network instability (Attempt {attempt+1}, Failures: {connection_error_count}). Executing Failover...")
                await asyncio.sleep(2.0)
                continue

            except Exception as e:
                logger.error(f"Unexpected error in Qwen Perception Engine: {e}")
                raise GradingSystemError(f"An unexpected error occurred during perception: {str(e)}")
