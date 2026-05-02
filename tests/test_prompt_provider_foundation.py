import asyncio
from pathlib import Path

import pytest

from src.prompts.cache_memory import InMemoryPromptCache
from src.prompts.exceptions import PromptTokenBudgetExceeded
from src.prompts.provider import PromptProviderService
from src.prompts.schemas import PromptInvalidationEvent, PromptResolveRequest, PromptVariable
from src.prompts.source_file import FilePromptSource


class FakeL2Cache:
    def __init__(self):
        self.data = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value, ttl_seconds: int):
        _ = ttl_seconds
        self.data[key] = value

    async def invalidate_prefix(self, prefix: str):
        keys = [k for k in self.data.keys() if k.startswith(prefix)]
        for k in keys:
            self.data.pop(k, None)
        return len(keys)


class BrokenL2Cache(FakeL2Cache):
    async def get(self, key: str):
        del key
        raise ConnectionError("redis unavailable")

    async def set(self, key: str, value, ttl_seconds: int):
        del key, value, ttl_seconds
        raise ConnectionError("redis unavailable")


class FakeBus:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def publish(self, event: PromptInvalidationEvent):
        await self.queue.put(event)

    async def subscribe(self):
        return None

    async def recv(self):
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            return None


def _make_provider(prompts_dir: Path) -> PromptProviderService:
    return PromptProviderService(
        source=FilePromptSource(prompts_dir),
        l1_cache=InMemoryPromptCache(ttl_seconds=2, swr_seconds=1),
        l2_cache=FakeL2Cache(),
        invalidation_bus=FakeBus(),
        pull_interval_seconds=1,
        l1_ttl_seconds=2,
        l2_ttl_seconds=10,
    )


def _make_provider_with_l2(prompts_dir: Path, l2_cache) -> PromptProviderService:
    return PromptProviderService(
        source=FilePromptSource(prompts_dir),
        l1_cache=InMemoryPromptCache(ttl_seconds=2, swr_seconds=1),
        l2_cache=l2_cache,
        invalidation_bus=FakeBus(),
        pull_interval_seconds=1,
        l1_ttl_seconds=2,
        l2_ttl_seconds=10,
    )


def _base_req(prompt_key: str, *, vars_):
    return PromptResolveRequest(
        prompt_key=prompt_key,
        model="qwen-vl-max",
        trace_id="trace-1",
        bucket_key="bucket-1",
        locale="zh-CN",
        variables=vars_,
        max_input_tokens=32768,
        reserve_output_tokens=1024,
    )


@pytest.mark.asyncio
async def test_multimodal_render_builds_openai_content():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "qwen.perception.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="student_homework"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
    )
    result = await provider.resolve(req)
    assert result.messages[0]["role"] == "system"
    user_content = result.messages[1]["content"]
    assert isinstance(user_content, list)
    assert any(isinstance(x, dict) and x.get("type") == "image_url" for x in user_content)
    await provider.stop()


@pytest.mark.asyncio
async def test_qwen_extract_prompt_preserves_handwritten_chinese_prose():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "qwen.perception.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="student_answer_regions"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
    )
    result = await provider.resolve(req)
    system_text = result.messages[0]["content"]
    user_content = result.messages[1]["content"]
    user_text = next(item["text"] for item in user_content if item.get("type") == "text")

    assert "手写文字完整转录" in system_text
    assert "绝对禁止只转录公式而忽略" in system_text
    assert "手写分式数字精读" in system_text
    assert "区分 4 与 7" in system_text
    assert "handwritten Chinese prose" in user_text
    await provider.stop()


@pytest.mark.asyncio
async def test_qwen_extract_prompt_requires_explicit_null_for_blank_slots():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "qwen.perception.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="student_answer_regions"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
    )
    result = await provider.resolve(req)
    system_text = result.messages[0]["content"]
    user_content = result.messages[1]["content"]
    user_text = next(item["text"] for item in user_content if item.get("type") == "text")

    assert "优先使用 `<student>null</student>`" in system_text
    assert "禁止输出空标签 `<student></student>`" in system_text
    assert "emit `<student>null</student>`" in user_text
    assert "<student></student>" in user_text
    await provider.stop()


@pytest.mark.asyncio
async def test_qwen_extract_prompt_handles_subtle_revisions():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "qwen.perception.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="student_answer_regions"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
    )
    result = await provider.resolve(req)
    system_text = result.messages[0]["content"]
    user_content = result.messages[1]["content"]
    user_text = next(item["text"] for item in user_content if item.get("type") == "text")

    assert "轻度涂改识别" in system_text
    assert "局部反复描粗" in system_text
    assert "旧答案残影与新答案拼接成一个 student token" in system_text
    assert "调协谐" in system_text
    assert "解调振谐" in system_text
    assert "一空一层原则" in system_text
    assert "同层字符自检" in system_text
    assert "lightly cancelled" in user_text
    assert "Do not concatenate abandoned and retained answers" in user_text
    assert "调协谐" in user_text
    assert "prefer omission over fusion" in user_text
    assert "same retained stroke layer" in user_text
    await provider.stop()


@pytest.mark.asyncio
async def test_qwen_extract_prompt_allows_local_worked_solution_block_tag():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "qwen.perception.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="student_answer_regions"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
    )
    result = await provider.resolve(req)
    system_text = result.messages[0]["content"]
    user_content = result.messages[1]["content"]
    user_text = next(item["text"] for item in user_content if item.get("type") == "text")

    assert "局部解答块标记" in system_text
    assert "单个 `<student>...</student>` 首尾包裹" in system_text
    assert "不适用于填空槽位、选择标记或短答案序列" in system_text
    assert "continuous multi-line handwritten solution block" in user_text
    assert "you may wrap that local student-written block" in user_text
    assert "Do not use this block-tag rule for fill-blank or choice-slot sequences" in user_text
    await provider.stop()


@pytest.mark.asyncio
async def test_deepseek_evaluate_prompt_requires_per_grading_point_output():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "deepseek.cognitive.evaluate",
        vars_=[
            PromptVariable(name="perception_ir_json", kind="text", value="{}"),
            PromptVariable(name="target_json_schema", kind="text", value="{}"),
            PromptVariable(name="rubric_json", kind="text", value='{"grading_points": []}'),
        ],
    )
    result = await provider.resolve(req)
    system_text = result.messages[0]["content"]

    assert "逐给分点输出规则" in system_text
    assert "grading_point_id" in system_text
    assert "total_score_deduction 必须等于" in system_text
    assert "atomic grading_points" in system_text
    assert "后续正确公式、代入或最终结果已经唯一覆盖该单元" in system_text
    assert "description 简短或受噪声污染" in system_text
    assert "解答题乱序容忍" in system_text
    assert "跨小问错位的人工复核出口" in system_text
    assert "同一子问内部的局部乱序容忍" in system_text
    assert "REJECTED_UNREADABLE" in system_text
    assert "局部解答块提示" in system_text
    assert "worked_solution_block_detected=true" in system_text
    await provider.stop()


@pytest.mark.asyncio
async def test_deepseek_rubric_prompt_requires_atomic_score_points():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "deepseek.cognitive.rubric",
        vars_=[
            PromptVariable(name="perception_ir_json", kind="text", value="{}"),
            PromptVariable(name="target_json_schema", kind="text", value="{}"),
        ],
    )
    result = await provider.resolve(req)
    system_text = result.messages[0]["content"]

    assert "给分点拆分规则" in system_text
    assert "1 分 atomic grading_points" in system_text
    assert "禁止把多个 1 分步骤压缩成一个概括性大点" in system_text
    assert "绝对不要输出只有“2”“π”“�”这类碎片或乱码" in system_text
    assert "方法二/等价方法" in system_text
    await provider.stop()


@pytest.mark.asyncio
async def test_prompt_cache_key_is_bound_to_variables():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req1 = _base_req(
        "qwen.perception.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="student_homework"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZV9pbWFnZV8x"),
        ],
    )
    req2 = _base_req(
        "qwen.perception.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="student_homework"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZV9pbWFnZV8y"),
        ],
    )
    await provider.resolve(req1)
    await provider.resolve(req2)
    l2_keys = list(provider._l2.data.keys())  # type: ignore[attr-defined]
    assert len(l2_keys) >= 2
    assert len(set(l2_keys)) == len(l2_keys)
    await provider.stop()


@pytest.mark.asyncio
async def test_token_budget_guard_rejects_oversized_prompt():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = PromptResolveRequest(
        prompt_key="qwen.perception.extract",
        model="qwen-vl-max",
        trace_id="trace-2",
        bucket_key="bucket-2",
        locale="zh-CN",
        variables=[
            PromptVariable(name="context_type", kind="text", value="X" * 20000),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
        max_input_tokens=200,
        reserve_output_tokens=50,
    )
    with pytest.raises(PromptTokenBudgetExceeded):
        await provider.resolve(req)
    await provider.stop()


@pytest.mark.asyncio
async def test_ab_bucket_uses_bucket_key_not_trace_id():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    vars_ = [
        PromptVariable(name="perception_ir_json", kind="text", value='{"readability_status":"CLEAR","elements":[],"global_confidence":1.0}'),
        PromptVariable(name="target_json_schema", kind="text", value='{"type":"object"}'),
        PromptVariable(name="rubric_json", kind="text", value=""),
    ]
    req1 = PromptResolveRequest(
        prompt_key="deepseek.cognitive.evaluate",
        model="deepseek-v4-flash",
        trace_id="trace-A",
        bucket_key="fixed-user-1",
        locale="zh-CN",
        variables=vars_,
        max_input_tokens=32768,
        reserve_output_tokens=1024,
    )
    req2 = PromptResolveRequest(
        prompt_key="deepseek.cognitive.evaluate",
        model="deepseek-v4-flash",
        trace_id="trace-B",
        bucket_key="fixed-user-1",
        locale="zh-CN",
        variables=vars_,
        max_input_tokens=32768,
        reserve_output_tokens=1024,
    )
    r1 = await provider.resolve(req1)
    r2 = await provider.resolve(req2)
    assert r1.variant_id == r2.variant_id
    await provider.stop()


@pytest.mark.asyncio
async def test_push_plus_pull_invalidation_clears_l1():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    req = _base_req(
        "qwen.layout.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="STUDENT_ANSWER"),
            PromptVariable(name="target_question_no", kind="text", value="13"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
    )
    first = await provider.resolve(req)
    assert first.cache_level in {"SOURCE", "L1", "L2"}
    await provider.invalidate(
        PromptInvalidationEvent(
            prompt_key="qwen.layout.extract",
            version_hash="changed-hash",
            source="test",
        )
    )
    second = await provider.resolve(req)
    assert second.asset_version == first.asset_version
    await provider.stop()


@pytest.mark.asyncio
async def test_forced_variant_and_lkg_mode_controls():
    provider = _make_provider(Path("configs/prompts"))
    await provider.start()
    vars_ = [
        PromptVariable(name="perception_ir_json", kind="text", value='{"readability_status":"CLEAR","elements":[],"global_confidence":1.0}'),
        PromptVariable(name="target_json_schema", kind="text", value='{"type":"object"}'),
        PromptVariable(name="rubric_json", kind="text", value=""),
    ]
    req = PromptResolveRequest(
        prompt_key="deepseek.cognitive.evaluate",
        model="deepseek-v4-flash",
        trace_id="trace-force",
        bucket_key="bucket-force",
        locale="zh-CN",
        variables=vars_,
        max_input_tokens=32768,
        reserve_output_tokens=1024,
    )
    result_a = await provider.resolve(req)
    provider.set_forced_variant(prompt_key="deepseek.cognitive.evaluate", variant_id="A")
    result_forced = await provider.resolve(req)
    assert result_forced.variant_id == "A"
    provider.set_lkg_mode(prompt_key="deepseek.cognitive.evaluate", enabled=True)
    result_lkg = await provider.resolve(req)
    assert result_lkg.cache_level == "LKG"
    assert result_lkg.asset_version == result_a.asset_version
    await provider.stop()


@pytest.mark.asyncio
async def test_resolve_degrades_when_l2_cache_unavailable():
    provider = _make_provider_with_l2(Path("configs/prompts"), BrokenL2Cache())
    await provider.start()
    req = _base_req(
        "qwen.layout.extract",
        vars_=[
            PromptVariable(name="context_type", kind="text", value="STUDENT_ANSWER"),
            PromptVariable(name="target_question_no", kind="text", value="13"),
            PromptVariable(name="image_1", kind="image_base64", value="ZmFrZQ=="),
        ],
    )
    result = await provider.resolve(req)
    assert result.asset_version
    assert result.variant_id
    assert result.cache_level in {"SOURCE", "L1"}
    await provider.stop()
