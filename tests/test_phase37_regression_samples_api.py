import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.db.client import init_db, create_task, update_task_status, submit_task_review
from src.api.dependencies import get_db_path


@pytest.fixture
def test_db_path(tmp_path):
    db_path = str(tmp_path / "phase37_regression.db")
    asyncio.run(init_db(db_path))
    return db_path


@pytest.fixture
def client_with_db(test_db_path):
    app.dependency_overrides[get_db_path] = lambda: test_db_path
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_regression_samples_only_returns_flagged_reviewed_items(client_with_db, test_db_path):
    task_id = "phase37-regression-001"
    asyncio.run(create_task(test_db_path, task_id))
    asyncio.run(
        update_task_status(
            test_db_path,
            task_id,
            "COMPLETED",
            grading_status="SCORED",
            review_status="PENDING_REVIEW",
        )
    )
    asyncio.run(
        submit_task_review(
            test_db_path,
            task_id,
            human_feedback_json={"before": {"score": 6}, "after": {"score": 8}, "diff": ["fix"]},
            is_regression_sample=True,
        )
    )

    resp = client_with_db.get("/api/v1/review/regression-samples?page=1&limit=20")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["task_id"] == task_id
    assert item["grading_status"] == "SCORED"
    assert item["review_status"] == "REVIEWED"
    assert item["human_feedback_json"]["after"]["score"] == 8


def test_regression_samples_excludes_non_regression_items(client_with_db, test_db_path):
    task_id = "phase37-regression-002"
    asyncio.run(create_task(test_db_path, task_id))
    asyncio.run(
        update_task_status(
            test_db_path,
            task_id,
            "COMPLETED",
            grading_status="REJECTED_UNREADABLE",
            review_status="PENDING_REVIEW",
        )
    )
    asyncio.run(
        submit_task_review(
            test_db_path,
            task_id,
            human_feedback_json={"before": {"score": 0}, "after": {"score": 0}, "diff": []},
            is_regression_sample=False,
        )
    )

    resp = client_with_db.get("/api/v1/review/regression-samples?page=1&limit=20")
    assert resp.status_code == 200
    data = resp.json()
    task_ids = [x["task_id"] for x in data["items"]]
    assert task_id not in task_ids

