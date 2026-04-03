import logging
import re
import json
import asyncio
from typing import Any

import openai
from pydantic import ValidationError
from src.cognitive.base import BaseCognitiveAgent
from src.core.config import settings
from src.core.connection_pool import CircuitBreakerKeyPool, AllKeysExhaustedError
from src.core.exceptions import GradingSystemError
from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError  # Phase 33
from src.core.runtime_router import get_runtime_router_controller
from src.core.trace_context import outbound_trace_headers, get_trace_id, get_task_id
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import PerceptionOutput
from src.schemas.rubric_ir import TeacherRubric
from src.prompts.provider import get_prompt_provider
from src.prompts.schemas import PromptResolveRequest, PromptVariable


logger = logging.getLogger(__name__)


class DeepSeekCognitiveEngine(BaseCognitiveAgent):
    """
    Implementation of the Cognitive Reasoning Engine using DeepSeek Reasoner (R1).
    Supports API Key pooling with circuit breaker logic (Phase 22.5).
    Phase 33: Global circuit breaker for API service-level protection.
    """

    def __init__(self):
        """Initialize a circuit-breaker-aware pool of clients."""
        keys = settings.parsed_deepseek_keys or ["MISSING"]
        self._key_pool = CircuitBreakerKeyPool("DeepSeekPool", keys)
        
        # Mapping: Key string -> AsyncOpenAI client
        self._clients = {
            key: openai.AsyncOpenAI(
                api_key=key,
                base_url="https://api.deepseek.com",
                timeout=400.0,
                max_retries=0 # Manual failover
            ) for key in keys
        }
        
        # Phase 33: Global circuit breaker for DeepSeek API service
        self._circuit_breaker = CircuitBreaker(
            name="deepseek_api_service",
            failure_threshold=5,
            recovery_timeout=90.0,      # Longer timeout for reasoning model
            success_threshold=2,
            expected_exceptions=(
                openai.APIError,
                openai.APIConnectionError,
                openai.RateLimitError,
                openai.InternalServerError,
            ),
        )
        self._prompt_provider = get_prompt_provider()
        self._last_runtime_telemetry: dict[str, Any] | None = None

    def _prompt_context(self) -> tuple[str, str]:
        trace_id = get_trace_id()
        if not trace_id or trace_id == "-":
            trace_id = "local-trace"
        task_id = get_task_id()
        bucket_key = task_id if task_id and task_id != "-" else trace_id
        return trace_id, bucket_key

    def _extract_json_content(self, text: str) -> str:
        """
        Robustly extracts JSON string from markdown blocks or raw text.
        Handles DeepSeek Reasoner's CoT (<think>) and potentially truncated markdown.
        """
        # 1. Try to match ```json ... ``` (greedy or non-greedy)
        json_block_pattern = r"```json\s*(.*?)\s*(?:```|$)"
        match = re.search(json_block_pattern, text, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
        
        # 2. Fallback: Find the first { and last }
        # This is more robust against markdown issues
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            return text[start_idx:end_idx+1].strip()
            
        # 3. Last ditch: Strip <think> and return remaining
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return cleaned

    def get_last_runtime_telemetry(self) -> dict[str, Any] | None:
        if self._last_runtime_telemetry is None:
            return None
        return dict(self._last_runtime_telemetry)

    async def evaluate_logic(
        self, 
        perception_data: PerceptionOutput, 
        rubric: TeacherRubric | None = None
    ) -> EvaluationReport:
        """Evaluate student work with configurable stream strategy and deterministic fallback."""
        ir_json_input = perception_data.model_dump_json()
        target_schema = json.dumps(EvaluationReport.model_json_schema(), indent=2)
        rubric_json = rubric.model_dump_json() if rubric is not None else ""
        trace_id, bucket_key = self._prompt_context()
        prompt_provider = getattr(self, "_prompt_provider", None) or get_prompt_provider()
        prompt_bundle = await prompt_provider.resolve(
            PromptResolveRequest(
                prompt_key="deepseek.cognitive.evaluate",
                model=settings.deepseek_model_name,
                trace_id=trace_id,
                bucket_key=bucket_key,
                locale="zh-CN",
                variables=[
                    PromptVariable(name="perception_ir_json", kind="text", value=ir_json_input),
                    PromptVariable(name="target_json_schema", kind="text", value=target_schema),
                    PromptVariable(name="rubric_json", kind="text", value=rubric_json),
                ],
                max_input_tokens=8192,
                reserve_output_tokens=1536,
            )
        )
        token_estimate = int(prompt_bundle.token_estimate)
        runtime_router = get_runtime_router_controller()
        
        max_retries = 15
        connection_error_count = 0
        MAX_CONNECTION_ERRORS = 1
        last_error_message = ""

        for attempt in range(max_retries + 1):
            full_raw_text = ""
            try:
                # 1. Get a healthy key from pool
                key_meta = self._key_pool.get_key_metadata()
                current_key = key_meta["key"]
                client = self._clients[current_key]

                # 2. Primary model path + deterministic fallback.
                # Stream mode is configurable to balance latency/caching vs token streaming.
                route_decision = runtime_router.decide_cognitive_route(
                    readability_status=perception_data.readability_status,
                    incoming_token_estimate=token_estimate,
                    requested_model=settings.deepseek_model_name,
                )
                should_degrade = connection_error_count >= MAX_CONNECTION_ERRORS or route_decision.force_degrade_to_chat
                if should_degrade:
                    fallback_reason = route_decision.reason
                    if connection_error_count >= MAX_CONNECTION_ERRORS and not route_decision.force_degrade_to_chat:
                        fallback_reason = "network_error_threshold"
                    logger.warning(
                        "Switching to deepseek-chat fallback (attempt=%s, net_failures=%s, reason=%s).",
                        attempt + 1,
                        connection_error_count,
                        fallback_reason,
                    )
                    model_to_use = "deepseek-chat"
                    use_stream = False
                else:
                    fallback_reason = None
                    model_to_use = route_decision.cognitive_model
                    use_stream = route_decision.stream

                # 3. Execute request
                if use_stream:
                    logger.info(
                        "llm_request_outbound",
                        extra={"extra_fields": {"component": "deepseek-engine", "model": model_to_use, "stream": True}},
                    )
                    # Phase 33: Wrap streaming API call with circuit breaker
                    @self._circuit_breaker
                    async def _protected_stream_call():
                        return await client.chat.completions.create(
                            model=model_to_use,
                            extra_headers=outbound_trace_headers(),
                            messages=prompt_bundle.messages,  # type: ignore[arg-type]
                            temperature=None,
                            stream=True
                        )
                    
                    stream = await _protected_stream_call()
                    
                    content_acc = ""
                    reasoning_acc = ""
                    async for chunk in stream:
                        choices = getattr(chunk, "choices", None) or []
                        if not choices:
                            continue
                        delta = getattr(choices[0], "delta", None)
                        if not delta:
                            continue
                        reasoning_piece = getattr(delta, "reasoning_content", None)
                        content_piece = getattr(delta, "content", None)
                        if reasoning_piece:
                            reasoning_acc += reasoning_piece
                        if content_piece:
                            content_acc += content_piece

                    if reasoning_acc:
                        full_raw_text += f"<think>\n{reasoning_acc}\n</think>\n"
                    full_raw_text += content_acc
                else:
                    logger.info(
                        "llm_request_outbound",
                        extra={"extra_fields": {"component": "deepseek-engine", "model": model_to_use, "stream": False}},
                    )
                    # Phase 33: Wrap non-streaming API call with circuit breaker
                    @self._circuit_breaker
                    async def _protected_call():
                        return await client.chat.completions.create(
                            model=model_to_use,
                            extra_headers=outbound_trace_headers(),
                            messages=prompt_bundle.messages,  # type: ignore[arg-type]
                            stream=False,
                            timeout=90.0
                        )
                    
                    response = await _protected_call()
                    full_raw_text = response.choices[0].message.content or ""

                connection_error_count = 0
                cleaned_json = self._extract_json_content(full_raw_text)
                if not cleaned_json:
                    raise json.JSONDecodeError("No JSON payload extracted from model response.", full_raw_text, 0)

                parsed_data = json.loads(cleaned_json)
                if "evaluation_report" in parsed_data and isinstance(parsed_data["evaluation_report"], dict):
                    parsed_data = parsed_data["evaluation_report"]
                elif len(parsed_data) == 1 and isinstance(list(parsed_data.values())[0], dict):
                    parsed_data = list(parsed_data.values())[0]
                runtime_router.record_event(
                    model=model_to_use,
                    success=True,
                    token_estimate=token_estimate,
                    fallback_used=(model_to_use == "deepseek-chat"),
                    reason="ok",
                )
                self._last_runtime_telemetry = {
                    "requested_model": settings.deepseek_model_name,
                    "model_used": model_to_use,
                    "route_reason": fallback_reason if should_degrade else "default",
                    "fallback_used": bool(model_to_use == "deepseek-chat"),
                    "fallback_reason": fallback_reason if should_degrade else None,
                    "prompt_key": "deepseek.cognitive.evaluate",
                    "prompt_asset_version": str(prompt_bundle.asset_version),
                    "prompt_variant_id": str(prompt_bundle.variant_id),
                    "prompt_cache_level": str(prompt_bundle.cache_level),
                    "prompt_token_estimate": int(prompt_bundle.token_estimate),
                    "succeeded": True,
                }
                return EvaluationReport.model_validate(parsed_data)

            except AllKeysExhaustedError as e:
                logger.error(f"FATAL: {e}")
                runtime_router.record_event(
                    model=settings.deepseek_model_name,
                    success=False,
                    token_estimate=token_estimate,
                    fallback_used=True,
                    reason="all_keys_exhausted",
                )
                self._last_runtime_telemetry = {
                    "requested_model": settings.deepseek_model_name,
                    "model_used": settings.deepseek_model_name,
                    "route_reason": "all_keys_exhausted",
                    "fallback_used": True,
                    "fallback_reason": "all_keys_exhausted",
                    "prompt_key": "deepseek.cognitive.evaluate",
                    "prompt_asset_version": str(prompt_bundle.asset_version),
                    "prompt_variant_id": str(prompt_bundle.variant_id),
                    "prompt_cache_level": str(prompt_bundle.cache_level),
                    "prompt_token_estimate": int(prompt_bundle.token_estimate),
                    "succeeded": False,
                }
                raise GradingSystemError("All DeepSeek API keys are rate-limited. System saturated.")
            
            except CircuitBreakerOpenError as e:
                # Phase 33: Circuit breaker OPEN, service degraded
                logger.error(f"DeepSeek API circuit breaker OPEN: {e}")
                runtime_router.record_event(
                    model=settings.deepseek_model_name,
                    success=False,
                    token_estimate=token_estimate,
                    fallback_used=True,
                    reason="circuit_open",
                )
                self._last_runtime_telemetry = {
                    "requested_model": settings.deepseek_model_name,
                    "model_used": settings.deepseek_model_name,
                    "route_reason": "circuit_open",
                    "fallback_used": True,
                    "fallback_reason": "circuit_open",
                    "prompt_key": "deepseek.cognitive.evaluate",
                    "prompt_asset_version": str(prompt_bundle.asset_version),
                    "prompt_variant_id": str(prompt_bundle.variant_id),
                    "prompt_cache_level": str(prompt_bundle.cache_level),
                    "prompt_token_estimate": int(prompt_bundle.token_estimate),
                    "succeeded": False,
                }
                raise GradingSystemError(
                    f"DeepSeek API service degraded. Circuit breaker active. {str(e)}"
                )

            except openai.RateLimitError:
                connection_error_count = 0
                logger.warning(f"Rate limit hit on DeepSeek Key. Tripping circuit breaker... (Attempt {attempt+1})")
                self._key_pool.report_429(current_key)
                runtime_router.record_event(
                    model=settings.deepseek_model_name,
                    success=False,
                    token_estimate=token_estimate,
                    fallback_used=False,
                    reason="rate_limit",
                )
                await asyncio.sleep(0.5)
                continue

            except (openai.APIConnectionError, openai.APITimeoutError) as net_err:
                connection_error_count += 1
                last_error_message = str(net_err)
                logger.warning(
                    "Network instability (attempt=%s, net_failures=%s): %s",
                    attempt + 1,
                    connection_error_count,
                    net_err,
                )
                runtime_router.record_event(
                    model=settings.deepseek_model_name,
                    success=False,
                    token_estimate=token_estimate,
                    fallback_used=False,
                    reason="network_error",
                )
                await asyncio.sleep(2.0)
                continue

            except openai.APIError as api_err:
                connection_error_count += 1
                last_error_message = str(api_err)
                logger.warning(
                    "API error (attempt=%s, net_failures=%s): %s",
                    attempt + 1,
                    connection_error_count,
                    api_err,
                )
                runtime_router.record_event(
                    model=settings.deepseek_model_name,
                    success=False,
                    token_estimate=token_estimate,
                    fallback_used=False,
                    reason="api_error",
                )
                await asyncio.sleep(2.0)
                continue

            except (json.JSONDecodeError, ValidationError) as parse_err:
                last_error_message = str(parse_err)
                logger.error(
                    "Response parse/validation failed (attempt=%s, model=%s): %s",
                    attempt + 1,
                    settings.deepseek_model_name if not should_degrade else "deepseek-chat",
                    parse_err,
                )
                logger.error("Raw Output Snippet: %s", full_raw_text[:200])
                connection_error_count += 1
                runtime_router.record_event(
                    model=settings.deepseek_model_name if not should_degrade else "deepseek-chat",
                    success=False,
                    token_estimate=token_estimate,
                    fallback_used=should_degrade,
                    reason="parse_error",
                )
                await asyncio.sleep(1.0)
                continue

            except Exception as e:
                error_text = str(e)
                last_error_message = error_text
                lowered = error_text.lower()
                if "incomplete chunked read" in lowered or "connection error" in lowered:
                    connection_error_count += 1
                    logger.warning(
                        "Transport-like exception treated as network failure (attempt=%s, net_failures=%s): %s",
                        attempt + 1,
                        connection_error_count,
                        e,
                    )
                    runtime_router.record_event(
                        model=settings.deepseek_model_name,
                        success=False,
                        token_estimate=token_estimate,
                        fallback_used=False,
                        reason="transport_error",
                    )
                    await asyncio.sleep(2.0)
                    continue
                logger.error(f"Logic evaluation failed: {e}")
                runtime_router.record_event(
                    model=settings.deepseek_model_name,
                    success=False,
                    token_estimate=token_estimate,
                    fallback_used=False,
                    reason="unexpected_error",
                )
                raise GradingSystemError(f"Cognitive evaluation error: {error_text}")

        raise GradingSystemError(
            "Cognitive evaluation failed after retries "
            f"(net_failures={connection_error_count}, last_error={last_error_message})"
        )

    async def generate_rubric(self, perception_data: PerceptionOutput) -> TeacherRubric:
        """Generates a structured TeacherRubric using DeepSeek Reasoner with Round-Robin pooling."""
        # Use existing key pool to get a client
        key_meta = self._key_pool.get_key_metadata()
        current_key = key_meta["key"]
        client = self._clients[current_key]
        
        ir_json_input = perception_data.model_dump_json()
        target_schema = json.dumps(TeacherRubric.model_json_schema(), indent=2)
        trace_id, bucket_key = self._prompt_context()
        prompt_provider = getattr(self, "_prompt_provider", None) or get_prompt_provider()
        prompt_bundle = await prompt_provider.resolve(
            PromptResolveRequest(
                prompt_key="deepseek.cognitive.rubric",
                model=settings.deepseek_model_name,
                trace_id=trace_id,
                bucket_key=bucket_key,
                locale="zh-CN",
                variables=[
                    PromptVariable(name="perception_ir_json", kind="text", value=ir_json_input),
                    PromptVariable(name="target_json_schema", kind="text", value=target_schema),
                ],
                max_input_tokens=8192,
                reserve_output_tokens=1536,
            )
        )
        
        try:
            logger.info("Generating TeacherRubric from model answer IR (Reasoner)...")
            logger.info(
                "llm_request_outbound",
                extra={"extra_fields": {"component": "deepseek-engine", "model": settings.deepseek_model_name, "purpose": "rubric"}},
            )
            
            # Phase 33: Wrap rubric extraction API call with circuit breaker
            @self._circuit_breaker
            async def _protected_rubric_call():
                return await client.chat.completions.create(
                    model=settings.deepseek_model_name,
                    extra_headers=outbound_trace_headers(),
                    messages=prompt_bundle.messages,  # type: ignore[arg-type]
                )
            
            response = await _protected_rubric_call()
            
            raw_content = response.choices[0].message.content
            cleaned_json = self._extract_json_content(raw_content)
            
            try:
                parsed_data = json.loads(cleaned_json)
                if "teacher_rubric" in parsed_data and isinstance(parsed_data["teacher_rubric"], dict):
                    parsed_data = parsed_data["teacher_rubric"]
                elif len(parsed_data) == 1 and isinstance(list(parsed_data.values())[0], dict):
                    parsed_data = list(parsed_data.values())[0]
                    
                return TeacherRubric.model_validate(parsed_data)
            except (json.JSONDecodeError, ValidationError) as ve:
                logger.error(f"Rubric validation failed: {ve}\nRaw snippet: {raw_content[:500]}")
                raise GradingSystemError(f"Rubric output validation error: {str(ve)}")

        except CircuitBreakerOpenError as e:
            # Phase 33: Circuit breaker OPEN during rubric generation
            logger.error(f"DeepSeek API circuit breaker OPEN during rubric generation: {e}")
            raise GradingSystemError(
                f"DeepSeek API service degraded during rubric extraction. {str(e)}"
            )
        
        except Exception as e:
            logger.error(f"Unexpected error in rubric generation: {e}")
            raise GradingSystemError(f"Unexpected rubric generation error: {str(e)}")
