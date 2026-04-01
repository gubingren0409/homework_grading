import asyncio
from pathlib import Path

import pytest

from src.db.client import create_task, get_task, init_db, update_task_status


@pytest.mark.asyncio
async def test_i02_status_is_monotonic_no_regression(tmp_path: Path):
    db_path = str(tmp_path / "status_monotonic.db")
    await init_db(db_path)
    task_id = "mono-001"
    await create_task(db_path, task_id)

    await update_task_status(db_path, task_id, "PROCESSING")
    await update_task_status(db_path, task_id, "PENDING")  # should be rejected
    task = await get_task(db_path, task_id)
    assert task is not None
    assert task["status"] == "PROCESSING"

    await update_task_status(db_path, task_id, "COMPLETED")
    await update_task_status(db_path, task_id, "PROCESSING")  # should be rejected
    task2 = await get_task(db_path, task_id)
    assert task2 is not None
    assert task2["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_i02_optional_fields_update_when_status_advances(tmp_path: Path):
    db_path = str(tmp_path / "status_fields.db")
    await init_db(db_path)
    task_id = "mono-002"
    await create_task(db_path, task_id)

    await update_task_status(
        db_path,
        task_id,
        "FAILED",
        error="boom",
        grading_status="REJECTED_UNREADABLE",
        review_status="PENDING_REVIEW",
        fallback_reason="PERCEPTION_SHORT_CIRCUIT:UNREADABLE",
    )
    task = await get_task(db_path, task_id)
    assert task is not None
    assert task["status"] == "FAILED"
    assert task["grading_status"] == "REJECTED_UNREADABLE"
    assert task["review_status"] == "PENDING_REVIEW"
