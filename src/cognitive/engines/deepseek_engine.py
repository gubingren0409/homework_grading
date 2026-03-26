import logging
import re
import json
import asyncio
import itertools
from typing import Any

import openai
from pydantic import ValidationError
from src.cognitive.base import BaseCognitiveAgent
from src.core.config import settings
from src.core.connection_pool import CircuitBreakerKeyPool, AllKeysExhaustedError
from src.core.exceptions import CognitiveRefusalError, GradingSystemError
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import PerceptionOutput
from src.schemas.rubric_ir import TeacherRubric


logger = logging.getLogger(__name__)


class DeepSeekCognitiveEngine(BaseCognitiveAgent):
    """
    Implementation of the Cognitive Reasoning Engine using DeepSeek Reasoner (R1).
    Supports API Key pooling with circuit breaker logic (Phase 22.5).
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
        
        # Phase 15 & 16: Hybrid Prompt with Grading Tolerance and Reasoner formatting
        self._format_instruction = (
            "\n\nCRITICAL CONSTRAINTS:\n"
            "1. You MUST return ONLY a valid JSON object enclosed within ```json and ``` markdown blocks.\n"
            "2. The JSON object MUST strictly conform to the target JSON Schema.\n"
            "3. DO NOT output anything outside the markdown blocks after your thinking process."
        )

        self._system_prompt_grading_base = (
            "You are an Educational-Grade Mathematical & Logical Verification Engine.\n"
            "Your objective is to evaluate student work based on Perception IR JSON data and a Rubric.\n\n"
            "【阅卷规则强化 (Grading Rule Reinforcement)】：\n"
            "1. 抗位移评判 (Anti-Shift Evaluation)：对于多空连答题，必须基于感知层提供的题号/空号（Key）进行一对一独立比对。即使存在漏答，也不得影响后续空位的独立判分。禁止根据数组索引顺序进行连坐扣分。\n"
            "2. OCR 与笔误容忍 (OCR Tolerance)：感知层提取的文本可能存在形近字/音近字识别错误（例如将物理符号 α 识别为 'a'，或将 '增大' 识别为 '增犬'）。如果提取文本与标准答案在物理语义上等价，或差异明显属于 VLM 视觉识别误差而非学生物理概念错误，必须判定为正确，不得扣分。\n"
            "3. 跳步与逻辑同构 (Step-skipping Acceptance)：若学生跳过基础代数化简或简单移项，只要上下文逻辑连贯、推导起止符合物理定律，判定为正确，【禁止以缺少中间步骤为由扣分】。\n"
            "4. 严格依据 Rubric 扣分：扣分必须精确映射到传入 Rubric 的 GradingPoint 分值，禁止自创扣分项。\n\n"
            "【人工复核触发逻辑】：\n"
            "当且仅当 IR 数据呈现极度混乱的逻辑断层，且你无法还原学生意图，或你高度怀疑上游视觉提取发生了灾难性乱码导致信息缺失时，必须将 requires_human_review 设为 true。\n\n"
            "【最高纪律：拒绝批改权】\n"
            "如果感知层提取的文本属于以下情况：极度残缺导致物理逻辑断裂、毫无意义的乱码、或与本题物理考点毫无关联（如纯粹的涂鸦提取物）。\n"
            "你必须立即停止推导，直接输出 JSON：\n"
            "将 `status` 设为 \"REJECTED_UNREADABLE\"，`total_score_deduction` 设为 0，`is_fully_correct` 设为 false，`step_evaluations` 设为空数组 []。并在总评语中简述拒绝原因。绝对禁止试图通过猜测来强行打分。"
        ) + self._format_instruction

        self._system_prompt_rubric_base = (
            "You are a Senior Science & Math Curriculum Expert. "
            "Your task is to analyze a standard model answer provided as a Perception IR JSON "
            "and generate a structured TeacherRubric."
        ) + self._format_instruction

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

    async def evaluate_logic(
        self, 
        perception_data: PerceptionOutput, 
        rubric: TeacherRubric | None = None
    ) -> EvaluationReport:
        """Evaluate student work with configurable stream strategy and deterministic fallback."""
        ir_json_input = perception_data.model_dump_json()
        user_content = f"Evaluate the following Perception IR data:\n{ir_json_input}"
        if rubric:
            user_content = f"Rubric:\n{rubric.model_dump_json()}\n\nEvaluate work:\n{ir_json_input}"
        
        target_schema = json.dumps(EvaluationReport.model_json_schema(), indent=2)
        final_user_content = f"{user_content}\n\nTarget JSON Schema:\n{target_schema}"
        
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
                should_degrade = connection_error_count >= MAX_CONNECTION_ERRORS
                should_bypass_reasoner = perception_data.readability_status == "HEAVILY_ALTERED"
                if should_degrade:
                    logger.warning(
                        "Switching to deepseek-chat fallback (attempt=%s, net_failures=%s).",
                        attempt + 1,
                        connection_error_count,
                    )
                    model_to_use = "deepseek-chat"
                    use_stream = False
                elif should_bypass_reasoner:
                    logger.warning(
                        "Bypassing reasoner for HEAVILY_ALTERED input (attempt=%s, confidence=%.2f). "
                        "Routing directly to deepseek-chat.",
                        attempt + 1,
                        perception_data.global_confidence,
                    )
                    model_to_use = "deepseek-chat"
                    use_stream = False
                else:
                    model_to_use = settings.deepseek_model_name
                    use_stream = settings.deepseek_use_stream

                # 3. Execute request
                if use_stream:
                    stream = await client.chat.completions.create(
                        model=model_to_use,
                        messages=[
                            {"role": "system", "content": self._system_prompt_grading_base},
                            {"role": "user", "content": final_user_content}
                        ],
                        temperature=None,
                        stream=True
                    )
                    
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
                    response = await client.chat.completions.create(
                        model=model_to_use,
                        messages=[
                            {"role": "system", "content": self._system_prompt_grading_base},
                            {"role": "user", "content": final_user_content}
                        ],
                        stream=False,
                        timeout=90.0
                    )
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

                return EvaluationReport.model_validate(parsed_data)

            except AllKeysExhaustedError as e:
                logger.error(f"FATAL: {e}")
                raise GradingSystemError("All DeepSeek API keys are rate-limited. System saturated.")

            except openai.RateLimitError:
                connection_error_count = 0
                logger.warning(f"Rate limit hit on DeepSeek Key. Tripping circuit breaker... (Attempt {attempt+1})")
                self._key_pool.report_429(current_key)
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
                    await asyncio.sleep(2.0)
                    continue
                logger.error(f"Logic evaluation failed: {e}")
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
        
        try:
            logger.info("Generating TeacherRubric from model answer IR (Reasoner)...")
            response = await client.chat.completions.create(
                model=settings.deepseek_model_name,
                messages=[
                    {"role": "system", "content": self._system_prompt_rubric_base},
                    {"role": "user", "content": f"Extract rubric from this model answer IR:\n{ir_json_input}\n\nTarget Schema:\n{target_schema}"}
                ]
            )
            
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

        except Exception as e:
            logger.error(f"Unexpected error in rubric generation: {e}")
            raise GradingSystemError(f"Unexpected rubric generation error: {str(e)}")
