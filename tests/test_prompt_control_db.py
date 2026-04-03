from pathlib import Path

import pytest

from src.db.client import (
    append_prompt_ops_audit,
    get_prompt_ab_config,
    get_prompt_control_state,
    init_db,
    list_prompt_ops_audit,
    upsert_prompt_ab_config,
    upsert_prompt_control_state,
)


@pytest.mark.asyncio
async def test_prompt_control_state_and_ab_config_persistence(tmp_path: Path):
    db_path = str(tmp_path / "prompt_control.db")
    await init_db(db_path)

    await upsert_prompt_control_state(
        db_path,
        prompt_key="deepseek.cognitive.evaluate",
        forced_variant_id="A",
        lkg_mode=True,
    )
    control = await get_prompt_control_state(db_path, prompt_key="deepseek.cognitive.evaluate")
    assert control["forced_variant_id"] == "A"
    assert control["lkg_mode"] is True

    await upsert_prompt_ab_config(
        db_path,
        prompt_key="deepseek.cognitive.evaluate",
        enabled=True,
        rollout_percentage=30,
        variant_weights={"A": 30, "B": 70},
        segment_prefixes=["tenant-1"],
        sticky_salt="salt-v1",
    )
    ab = await get_prompt_ab_config(db_path, prompt_key="deepseek.cognitive.evaluate")
    assert ab["enabled"] is True
    assert ab["rollout_percentage"] == 30
    assert ab["variant_weights"]["A"] == 30


@pytest.mark.asyncio
async def test_prompt_ops_audit_append_and_list(tmp_path: Path):
    db_path = str(tmp_path / "prompt_audit.db")
    await init_db(db_path)
    await append_prompt_ops_audit(
        db_path,
        trace_id="trace-1",
        operator_id="ops-user",
        action="prompt_refresh",
        prompt_key="qwen.layout.extract",
        payload_json={"ok": True},
    )
    rows = await list_prompt_ops_audit(db_path, prompt_key="qwen.layout.extract", limit=20, offset=0)
    assert len(rows) == 1
    assert rows[0]["action"] == "prompt_refresh"
