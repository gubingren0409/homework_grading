from pathlib import Path

import pytest

from src.db.client import (
    get_prompt_cache_level_stats,
    get_runtime_telemetry_fallback_stats,
    get_runtime_telemetry_model_hits,
    init_db,
    upsert_task_runtime_telemetry,
)


@pytest.mark.asyncio
async def test_upsert_runtime_telemetry_and_aggregate(tmp_path: Path):
    db_path = str(tmp_path / "runtime_telemetry.db")
    await init_db(db_path)

    await upsert_task_runtime_telemetry(
        db_path,
        task_id="t1",
        trace_id="trace-1",
        requested_model="deepseek-reasoner",
        model_used="deepseek-chat",
        route_reason="network_error_threshold",
        fallback_used=True,
        fallback_reason="network_error_threshold",
        prompt_key="deepseek.cognitive.evaluate",
        prompt_asset_version="v1",
        prompt_variant_id="A",
        prompt_cache_level="L1",
        prompt_token_estimate=120,
        succeeded=True,
    )
    await upsert_task_runtime_telemetry(
        db_path,
        task_id="t2",
        trace_id="trace-2",
        requested_model="deepseek-reasoner",
        model_used="deepseek-reasoner",
        route_reason="default",
        fallback_used=False,
        fallback_reason=None,
        prompt_key="deepseek.cognitive.evaluate",
        prompt_asset_version="v1",
        prompt_variant_id="A",
        prompt_cache_level="SOURCE",
        prompt_token_estimate=150,
        succeeded=True,
    )

    model_hits = await get_runtime_telemetry_model_hits(db_path)
    assert model_hits["deepseek-chat"] == 1
    assert model_hits["deepseek-reasoner"] == 1

    fallback_stats = await get_runtime_telemetry_fallback_stats(db_path)
    assert fallback_stats["total_count"] == 2
    assert fallback_stats["fallback_count"] == 1
    assert fallback_stats["reason_hits"]["network_error_threshold"] == 1

    cache_stats = await get_prompt_cache_level_stats(db_path)
    assert cache_stats["l1_count"] == 1
    assert cache_stats["source_count"] == 1
