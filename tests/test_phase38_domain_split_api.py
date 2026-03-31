import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import app
from src.api.dependencies import get_db_path
from src.db.client import (
    init_db,
    create_task,
    update_task_status,
    create_hygiene_interception_record,
)


def _setup_client(db_path: str) -> TestClient:
    app.dependency_overrides[get_db_path] = lambda: db_path
    return TestClient(app)


def test_phase38_hygiene_endpoints(tmp_path: Path):
    db_path = str(tmp_path / "phase38_hygiene.db")
    asyncio.run(init_db(db_path))

    asyncio.run(
        create_hygiene_interception_record(
            db_path,
            trace_id="trace-001",
            task_id="task-001",
            interception_node="unreadable",
            raw_image_path="file:///tmp/raw-a.jpg",
            action="manual_review",
        )
    )
    asyncio.run(
        create_hygiene_interception_record(
            db_path,
            trace_id="trace-002",
            task_id="task-002",
            interception_node="blank",
            raw_image_path="file:///tmp/raw-b.jpg",
            action="manual_review",
        )
    )

    client = _setup_client(db_path)
    try:
        resp = client.get("/api/v1/hygiene/interceptions?page=1&limit=20")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 2
        record_id = items[0]["id"]

        update_resp = client.post(
            f"/api/v1/hygiene/interceptions/{record_id}/action",
            json={"action": "discard"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["action"] == "discard"

        bulk_resp = client.post(
            "/api/v1/hygiene/interceptions/bulk-action",
            json={"record_ids": [x["id"] for x in items], "action": "manual_review"},
        )
        assert bulk_resp.status_code == 200
        assert bulk_resp.json()["updated_count"] >= 2
    finally:
        app.dependency_overrides.clear()


def test_phase38_annotation_feedback_endpoint_accepts_valid_anchor(tmp_path: Path):
    db_path = str(tmp_path / "phase38_annotation_ok.db")
    asyncio.run(init_db(db_path))
    task_id = "phase38-ok-001"
    asyncio.run(create_task(db_path, task_id))
    asyncio.run(
        update_task_status(
            db_path,
            task_id,
            "COMPLETED",
            grading_status="SCORED",
            review_status="NOT_REQUIRED",
        )
    )

    client = _setup_client(db_path)
    payload = {
        "task_id": task_id,
        "region_id": "region_1",
        "region_type": "answer_region",
        "image_width": 1000,
        "image_height": 1000,
        "bbox": {"x1": 100, "y1": 100, "x2": 400, "y2": 300},
        "teacher_text_feedback": "第2步符号错误。",
        "expected_score": 8.0,
        "perception_ir_snapshot": {
            "context_type": "STUDENT_ANSWER",
            "regions": [
                {
                    "target_id": "region_1",
                    "region_type": "answer_region",
                    "bbox": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.4},
                }
            ],
        },
        "cognitive_ir_snapshot": {
            "status": "SCORED",
            "step_evaluations": [
                {"reference_element_id": "region_1", "is_correct": False, "error_type": "CALCULATION"}
            ],
            "overall_feedback": "存在计算错误",
        },
        "is_integrated_to_dataset": False,
    }
    try:
        resp = client.post("/api/v1/annotations/feedback", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ACCEPTED"
        assert data["task_id"] == task_id
        assert data["region_id"] == "region_1"

        list_resp = client.get("/api/v1/annotations/assets?page=1&limit=20")
        assert list_resp.status_code == 200
        assets = list_resp.json()
        assert len(assets) >= 1
        assert any(a["task_id"] == task_id for a in assets)
    finally:
        app.dependency_overrides.clear()


def test_phase38_annotation_feedback_rejects_out_of_bounds_anchor(tmp_path: Path):
    db_path = str(tmp_path / "phase38_annotation_reject.db")
    asyncio.run(init_db(db_path))
    task_id = "phase38-bad-001"
    asyncio.run(create_task(db_path, task_id))
    asyncio.run(
        update_task_status(
            db_path,
            task_id,
            "COMPLETED",
            grading_status="SCORED",
            review_status="NOT_REQUIRED",
        )
    )

    client = _setup_client(db_path)
    payload = {
        "task_id": task_id,
        "region_id": "region_1",
        "region_type": "answer_region",
        "image_width": 1000,
        "image_height": 1000,
        "bbox": {"x1": 50, "y1": 50, "x2": 800, "y2": 900},
        "teacher_text_feedback": "该框明显越界。",
        "expected_score": 5.0,
        "perception_ir_snapshot": {
            "context_type": "STUDENT_ANSWER",
            "regions": [
                {
                    "target_id": "region_1",
                    "region_type": "answer_region",
                    "bbox": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.4, "y_max": 0.3},
                }
            ],
        },
        "cognitive_ir_snapshot": {
            "status": "SCORED",
            "step_evaluations": [
                {"reference_element_id": "region_1", "is_correct": False, "error_type": "LOGIC"}
            ],
            "overall_feedback": "映射偏差",
        },
        "is_integrated_to_dataset": False,
    }
    try:
        resp = client.post("/api/v1/annotations/feedback", json=payload)
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
