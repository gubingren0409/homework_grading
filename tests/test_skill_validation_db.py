import asyncio
from pathlib import Path

import pytest

from src.db.client import init_db, insert_skill_validation_records


@pytest.mark.asyncio
async def test_insert_skill_validation_records(tmp_path: Path):
    db_path = str(tmp_path / "skills.db")
    await init_db(db_path)

    inserted = await insert_skill_validation_records(
        db_path,
        [
            {
                "task_id": "t1",
                "student_id": "s1",
                "question_id": "q1",
                "checker": "e2b",
                "status": "ok",
                "confidence": 0.9,
                "details_json": {"matched": True},
            }
        ],
    )
    assert inserted == 1


@pytest.mark.asyncio
async def test_insert_skill_validation_records_rejects_status(tmp_path: Path):
    db_path = str(tmp_path / "skills_invalid.db")
    await init_db(db_path)

    with pytest.raises(ValueError):
        await insert_skill_validation_records(
            db_path,
            [
                {
                    "task_id": "t1",
                    "student_id": "s1",
                    "question_id": "q1",
                    "checker": "e2b",
                    "status": "unknown",
                    "confidence": 0.9,
                    "details_json": {"matched": False},
                }
            ],
        )
