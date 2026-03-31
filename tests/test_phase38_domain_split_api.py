import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import app
from src.api.dependencies import get_db_path
from src.db.client import (
    init_db,
    create_task,
    update_task_status,
    create_hygiene_interception_record,
    migrate_drop_legacy_review_columns,
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


def test_phase39_legacy_columns_are_physically_dropped_via_migration(tmp_path: Path):
    db_path = str(tmp_path / "phase39_drop_columns.db")
    asyncio.run(init_db(db_path))

    # Recreate historical legacy columns to simulate pre-Phase39 database.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN human_feedback_json TEXT")
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN is_regression_sample INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    finally:
        conn.close()

    # Run explicit migration and verify physical drop.
    asyncio.run(migrate_drop_legacy_review_columns(db_path))

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
    finally:
        conn.close()

    assert "human_feedback_json" not in columns
    assert "is_regression_sample" not in columns


def test_phase39_init_db_auto_migrates_legacy_columns(tmp_path: Path):
    db_path = str(tmp_path / "phase39_auto_migrate.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                grading_status TEXT,
                celery_task_id TEXT,
                rubric_id TEXT,
                error_message TEXT,
                review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
                human_feedback_json TEXT,
                is_regression_sample INTEGER NOT NULL DEFAULT 0,
                fallback_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tasks
            (task_id, status, grading_status, review_status, human_feedback_json, is_regression_sample)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("legacy-task-1", "COMPLETED", "SCORED", "REVIEWED", '{"legacy": true}', 1),
        )
        conn.commit()
    finally:
        conn.close()

    asyncio.run(init_db(db_path))

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        row = conn.execute("SELECT task_id, status, grading_status FROM tasks WHERE task_id = ?", ("legacy-task-1",)).fetchone()
    finally:
        conn.close()

    assert "human_feedback_json" not in columns
    assert "is_regression_sample" not in columns
    assert row == ("legacy-task-1", "COMPLETED", "SCORED")


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


def test_phase38_annotation_feedback_upsert_on_same_trace_region(tmp_path: Path):
    db_path = str(tmp_path / "phase38_annotation_upsert.db")
    asyncio.run(init_db(db_path))
    task_id = "phase38-upsert-001"
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
    base_payload = {
        "task_id": task_id,
        "region_id": "region_1",
        "region_type": "answer_region",
        "image_width": 1000,
        "image_height": 1000,
        "bbox": {"x1": 100, "y1": 100, "x2": 400, "y2": 300},
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
        first = dict(base_payload)
        first["teacher_text_feedback"] = "第一版反馈"
        resp1 = client.post(
            "/api/v1/annotations/feedback",
            json=first,
            headers={"X-Trace-Id": "trace-upsert-1"},
        )
        assert resp1.status_code == 200

        second = dict(base_payload)
        second["teacher_text_feedback"] = "第二版反馈（覆盖）"
        second["expected_score"] = 9.0
        resp2 = client.post(
            "/api/v1/annotations/feedback",
            json=second,
            headers={"X-Trace-Id": "trace-upsert-1"},
        )
        assert resp2.status_code == 200

        list_resp = client.get("/api/v1/annotations/assets?page=1&limit=20")
        assert list_resp.status_code == 200
        assets = list_resp.json()
        same = [a for a in assets if a["task_id"] == task_id and a["region_id"] == "region_1"]
        assert len(same) == 1
        assert same[0]["teacher_text_feedback"] == "第二版反馈（覆盖）"
        assert same[0]["expected_score"] == 9.0
    finally:
        app.dependency_overrides.clear()


def test_phase39_view_b_e2e_anchor_to_asset_roundtrip(tmp_path: Path):
    db_path = str(tmp_path / "phase39_view_b_e2e.db")
    asyncio.run(init_db(db_path))
    task_id = "phase39-canvas-001"
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
        "region_id": "region_canvas_1",
        "region_type": "answer_region",
        "image_width": 2000,
        "image_height": 1000,
        "bbox": {"x1": 220, "y1": 120, "x2": 980, "y2": 620},
        "teacher_text_feedback": "第3步推导遗漏边界条件，扣2分。",
        "expected_score": 8.0,
        "perception_ir_snapshot": {
            "context_type": "STUDENT_ANSWER",
            "regions": [
                {
                    "target_id": "region_canvas_1",
                    "region_type": "answer_region",
                    "bbox": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.8},
                }
            ],
        },
        "cognitive_ir_snapshot": {
            "status": "SCORED",
            "step_evaluations": [
                {"reference_element_id": "region_canvas_1", "is_correct": False, "error_type": "LOGIC"}
            ],
            "overall_feedback": "存在局部逻辑缺陷",
        },
        "is_integrated_to_dataset": False,
    }

    try:
        submit_resp = client.post("/api/v1/annotations/feedback", json=payload)
        assert submit_resp.status_code == 200

        list_resp = client.get(f"/api/v1/annotations/assets?task_id={task_id}&page=1&limit=20")
        assert list_resp.status_code == 200
        assets = list_resp.json()
        assert len(assets) == 1

        asset = assets[0]
        assert asset["region_id"] == "region_canvas_1"
        assert asset["bbox_coordinates"] == [220.0, 120.0, 980.0, 620.0]
        assert asset["teacher_text_feedback"] == "第3步推导遗漏边界条件，扣2分。"
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
