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


def _base_req(prompt_key: str, *, vars_):
    return PromptResolveRequest(
        prompt_key=prompt_key,
        model="qwen-vl-max",
        trace_id="trace-1",
        bucket_key="bucket-1",
        locale="zh-CN",
        variables=vars_,
        max_input_tokens=4096,
        reserve_output_tokens=512,
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
        model="deepseek-reasoner",
        trace_id="trace-A",
        bucket_key="fixed-user-1",
        locale="zh-CN",
        variables=vars_,
        max_input_tokens=4096,
        reserve_output_tokens=512,
    )
    req2 = PromptResolveRequest(
        prompt_key="deepseek.cognitive.evaluate",
        model="deepseek-reasoner",
        trace_id="trace-B",
        bucket_key="fixed-user-1",
        locale="zh-CN",
        variables=vars_,
        max_input_tokens=4096,
        reserve_output_tokens=512,
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
