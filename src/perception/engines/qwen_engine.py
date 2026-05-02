import base64
import logging
import re
import json
import asyncio
from typing import Any, Dict, Optional, Sequence
from io import BytesIO

import openai
from PIL import Image
from src.core.config import settings
from src.core.connection_pool import CircuitBreakerKeyPool, AllKeysExhaustedError
from src.core.exceptions import GradingSystemError
from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError  # Phase 33
from src.core.trace_context import outbound_trace_headers, get_task_id, get_trace_id
from src.perception.base import BasePerceptionEngine
from src.orchestration.reference_image_description import reference_output_to_dense_description
from src.schemas.perception_ir import PerceptionOutput, LayoutIR
from src.prompts.provider import get_prompt_provider
from src.prompts.schemas import PromptResolveRequest, PromptVariable


logger = logging.getLogger(__name__)


class QwenVLMPerceptionEngine(BasePerceptionEngine):
    """
    Implementation of the Visual Perception Engine using Qwen-VL.
    Supports API Key pooling and dynamic circuit breaker (Phase 22.5).
    Phase 33: Global circuit breaker for API service-level protection.
    """

    def __init__(self):
        """Initialize the pool of circuit-breaker-aware clients for Qwen-VL."""
        keys = settings.parsed_qwen_keys or []
        if not keys:
            logger.warning("No Qwen API keys configured — perception calls will fail at runtime")
            keys = ["MISSING"]
        self._key_pool = CircuitBreakerKeyPool("QwenPool", keys)
        
        _timeout = settings.qwen_api_timeout_seconds
        logger.info(f"Initializing Qwen engine: {len(keys)} key(s), timeout={_timeout}s, "
                     f"max_tokens={settings.qwen_max_output_tokens}, max_retries={settings.qwen_max_retries}")
        self._clients = {
            key: openai.AsyncOpenAI(
                api_key=key,
                base_url=settings.qwen_base_url,
                timeout=_timeout,
                max_retries=0 # Manual failover
            ) for key in keys
        }

        self._prompt_provider = get_prompt_provider()
        self._api_semaphore = asyncio.Semaphore(settings.effective_qwen_api_max_concurrency)
        
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
        self._batch_fallback_events: list[dict[str, Any]] = []

    def drain_batch_fallback_events(self) -> list[dict[str, Any]]:
        events = list(self._batch_fallback_events)
        self._batch_fallback_events.clear()
        return events

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

    def _extract_json_object_candidate(self, text: str) -> str:
        """
        Extract the most likely JSON object payload from model text.
        Handles fenced-json and polluted prefix/suffix text.
        """
        cleaned = self._clean_json_text(text)
        if cleaned.startswith("{") and cleaned.endswith("}"):
            return cleaned

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return cleaned[start:end + 1].strip()

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1].strip()

        return cleaned

    def _decode_json_object(self, text: str) -> Dict[str, Any]:
        candidate = self._extract_json_object_candidate(text)
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            raise ValueError("top-level JSON payload must be an object")
        return parsed

    def _sanitize_coordinates(self, data: dict) -> dict:
        """Intercepts and clips VLM hallucinated coordinates."""
        if "elements" not in data or not isinstance(data["elements"], list):
            return data
        sanitized_elements = []
        for elem in data["elements"]:
            if not isinstance(elem, dict):
                continue
            content = str(elem.get("raw_content") or "").strip()
            content_type = str(elem.get("content_type") or "")
            if content_type in {"image_diagram", "image", "table"} and len(content) < 10:
                if not content:
                    continue
                elem["content_type"] = "plain_text"
            if "bbox" in elem and elem["bbox"]:
                if isinstance(elem["bbox"], list):
                    elem["bbox"] = [max(0.0, min(1.0, float(c))) for c in elem["bbox"]]
                elif isinstance(elem["bbox"], dict):
                    for k, v in elem["bbox"].items():
                        if isinstance(v, (int, float)):
                            elem["bbox"][k] = max(0.0, min(1.0, float(v)))
            sanitized_elements.append(elem)
        data["elements"] = sanitized_elements
        return data

    def _image_dimensions(self, image_bytes: bytes) -> tuple[int, int]:
        with Image.open(BytesIO(image_bytes)) as img:
            return img.size  # (width, height)

    def _prompt_context(self) -> tuple[str, str]:
        trace_id = get_trace_id()
        if not trace_id or trace_id == "-":
            trace_id = "local-trace"
        task_id = get_task_id()
        bucket_key = task_id if task_id and task_id != "-" else trace_id
        return trace_id, bucket_key

    async def _resolve_prompt_messages(
        self,
        *,
        prompt_key: str,
        variables: Sequence[PromptVariable],
    ) -> list[dict]:
        trace_id, bucket_key = self._prompt_context()
        prompt_bundle = await self._prompt_provider.resolve(
            PromptResolveRequest(
                prompt_key=prompt_key,
                model=settings.qwen_model_name,
                trace_id=trace_id,
                bucket_key=bucket_key,
                locale="zh-CN",
                variables=list(variables),
                max_input_tokens=settings.prompt_max_input_tokens,
                reserve_output_tokens=settings.prompt_reserve_output_tokens,
            )
        )
        return prompt_bundle.messages

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
        prompt_key: str,
        prompt_variables: Sequence[PromptVariable],
        max_tokens: int | None = None,
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
    ) -> Dict[str, Any]:
        """
        Shared low-level JSON call for Phase 35 dual-mode perception.
        """
        if not settings.llm_egress_enabled:
            raise GradingSystemError("LLM egress disabled by configuration (LLM_EGRESS_ENABLED=false)")
        effective_max_tokens = max_tokens or settings.qwen_max_output_tokens
        connection_error_count = 0
        max_retries = settings.qwen_max_retries
        max_connection_errors = settings.qwen_max_connection_errors
        last_parse_error: Optional[str] = None
        messages = await self._resolve_prompt_messages(
            prompt_key=prompt_key,
            variables=prompt_variables,
        )

        max_attempts = max(max_retries + 1, len(self._key_pool.keys_metadata))
        for attempt in range(max_attempts):
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
                        request_options: dict[str, Any] = {}
                        if timeout_seconds is not None:
                            request_options["timeout"] = timeout_seconds
                        return await client.chat.completions.create(
                            model=settings.qwen_model_name,
                            extra_headers=outbound_trace_headers(),
                            messages=messages,  # type: ignore[arg-type]
                            temperature=temperature,
                            max_tokens=effective_max_tokens,
                            response_format={"type": "json_object"},
                            **request_options,
                        )

                    response = await _protected_call()
                    connection_error_count = 0

                    raw = response.choices[0].message.content
                    if not raw:
                        raise GradingSystemError("Received empty response from Qwen-VL.")
                    try:
                        return self._decode_json_object(raw)
                    except (json.JSONDecodeError, ValueError) as dec_err:
                        last_parse_error = str(dec_err)
                        logger.warning(
                            "qwen_json_parse_failed prompt_key=%s attempt=%s error=%s raw_snippet=%s",
                            prompt_key,
                            attempt + 1,
                            dec_err,
                            raw[:240].replace("\n", " "),
                        )
                        await asyncio.sleep(0.5)
                        continue

            except AllKeysExhaustedError:
                raise
            except CircuitBreakerOpenError:
                raise
            except openai.RateLimitError:
                connection_error_count = 0
                self._key_pool.report_429(current_key)
                await asyncio.sleep(0.5)
                continue
            except openai.BadRequestError as bad_req:
                if _is_qwen_key_access_error(bad_req):
                    connection_error_count = 0
                    self._key_pool.report_429(current_key, cooldown_seconds=3600)
                    logger.warning("qwen key access denied; cooling current key and trying next key")
                    await asyncio.sleep(0.5)
                    continue
                raise GradingSystemError(f"An unexpected error occurred during perception: {str(bad_req)}")
            except (openai.APIConnectionError, openai.APITimeoutError) as net_err:
                connection_error_count += 1
                if connection_error_count > max_connection_errors:
                    raise GradingSystemError(f"Persistent network instability for Qwen: {str(net_err)}")
                await asyncio.sleep(2.0)
                continue
            except Exception as e:
                raise GradingSystemError(f"An unexpected error occurred during perception: {str(e)}")

        if last_parse_error:
            raise GradingSystemError(
                f"VLM JSON decode failed after retries (prompt_key={prompt_key}): {last_parse_error}"
            )
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
        target_value = target_question_no if target_question_no else "AUTO"

        raw = await self._call_qwen_json(
            prompt_key="qwen.layout.extract",
            prompt_variables=[
                PromptVariable(name="context_type", kind="text", value=context_type),
                PromptVariable(name="target_question_no", kind="text", value=target_value),
                PromptVariable(name="image_1", kind="image_base64", value=base64_image),
            ],
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
        return await self._process_image_with_context(
            image_bytes,
            context_type="student_homework",
        )

    async def _process_image_with_context(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
    ) -> PerceptionOutput:
        """
        Asynchronously processes raw image bytes using Qwen-VL with Circuit-Breaker pooling (Phase 22.6).
        """
        base64_image = self._encode_image(image_bytes)
        raw = await self._call_qwen_json(
            prompt_key="qwen.perception.extract",
            prompt_variables=[
                PromptVariable(name="context_type", kind="text", value=context_type),
                PromptVariable(name="image_1", kind="image_base64", value=base64_image),
            ],
            temperature=0.01,
        )
        try:
            sanitized_dict = self._sanitize_coordinates(raw)
            return PerceptionOutput.model_validate(sanitized_dict)
        except Exception as ve:
            logger.error(f"Schema validation failed: {ve}\nRaw Output: {raw}")
            raise GradingSystemError(f"VLM output failed schema validation: {str(ve)}")

    async def process_images(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str = "student_homework",
    ) -> list[PerceptionOutput]:
        if not image_bytes_list:
            return []
        if context_type in {"student_answer_regions", "REFERENCE"}:
            return await asyncio.gather(
                *[
                    self._process_image_with_context(
                        image_bytes,
                        context_type=context_type,
                    )
                    for image_bytes in image_bytes_list
                ]
            )
        if len(image_bytes_list) == 1:
            return [
                await self._process_image_with_context(
                    image_bytes_list[0],
                    context_type=context_type,
                )
            ]

        prompt_variables = [
            PromptVariable(name="context_type", kind="text", value=context_type),
            PromptVariable(name="image_count", kind="text", value=str(len(image_bytes_list))),
            PromptVariable(
                name="image_manifest",
                kind="text",
                value="\n".join(
                    f"image_{index}: crop_index={index}"
                    for index in range(1, len(image_bytes_list) + 1)
                ),
            ),
        ]
        prompt_variables.extend(
            PromptVariable(
                name=f"image_{index}",
                kind="image_base64",
                value=self._encode_image(image_bytes),
            )
            for index, image_bytes in enumerate(image_bytes_list, start=1)
        )

        try:
            raw = await self._call_qwen_json(
                prompt_key="qwen.perception.batch_extract",
                prompt_variables=prompt_variables,
                temperature=0.01,
                timeout_seconds=settings.qwen_batch_api_timeout_seconds,
            )
            outputs = raw.get("outputs")
            if not isinstance(outputs, list) or len(outputs) != len(image_bytes_list):
                raise GradingSystemError(
                    "Qwen batch perception output count does not match input image count"
                )
            perception_outputs: list[PerceptionOutput] = []
            for output in outputs:
                if not isinstance(output, dict):
                    raise GradingSystemError("Qwen batch perception output item is not an object")
                perception_outputs.append(
                    PerceptionOutput.model_validate(self._sanitize_coordinates(output))
                )
            return perception_outputs
        except (GradingSystemError, TypeError, ValueError, KeyError) as exc:
            logger.warning("qwen batch perception failed, falling back to per-image calls: %s", exc)
            self._batch_fallback_events.append(
                {
                    "context_type": context_type,
                    "image_count": len(image_bytes_list),
                    "reason": str(exc),
                }
            )
            await self._circuit_breaker.reset()
            return await asyncio.gather(
                *[
                    self._process_image_with_context(image_bytes, context_type=context_type)
                    for image_bytes in image_bytes_list
                ]
            )

    async def describe_reference_images(self, image_bytes_list: list[bytes]) -> list[str]:
        outputs = await self.process_images(image_bytes_list, context_type="REFERENCE")
        return [reference_output_to_dense_description(output) for output in outputs]

    @staticmethod
    def reference_output_to_dense_description(output: PerceptionOutput) -> str:
        return reference_output_to_dense_description(output)


def _is_qwen_key_access_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "access denied" in text or "account is in good standing" in text
