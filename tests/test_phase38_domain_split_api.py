import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import app
from src.api.dependencies import get_db_path
from src.db.client import (
    init_db,
    create_task,
    insert_grading_results,
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


def test_phaseD2_review_pending_workbench_filter_and_sort(tmp_path: Path):
    db_path = str(tmp_path / "phaseD2_pending_workbench.db")
    asyncio.run(init_db(db_path))
    asyncio.run(create_task(db_path, "review-task-a"))
    asyncio.run(create_task(db_path, "review-task-b"))
    asyncio.run(
        update_task_status(
            db_path,
            "review-task-a",
            "COMPLETED",
            grading_status="REJECTED_UNREADABLE",
            review_status="PENDING_REVIEW",
            fallback_reason="PERCEPTION_SHORT_CIRCUIT:UNREADABLE",
        )
    )
    asyncio.run(
        update_task_status(
            db_path,
            "review-task-b",
            "COMPLETED",
            grading_status="SCORED",
            review_status="PENDING_REVIEW",
        )
    )

    client = _setup_client(db_path)
    try:
        resp = client.get(
            "/api/v1/review/pending-workbench"
            "?status=REJECTED_UNREADABLE&task_id=review-task-a&sort_by=task_id&sort_direction=asc&page=1&limit=20"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["limit"] == 20
        assert len(data["items"]) == 1
        assert data["items"][0]["task_id"] == "review-task-a"
        assert data["items"][0]["review_status"] == "PENDING_REVIEW"
    finally:
        app.dependency_overrides.clear()


def test_phaseD2_review_annotation_assets_list_and_detail(tmp_path: Path):
    db_path = str(tmp_path / "phaseD2_annotation_assets.db")
    asyncio.run(init_db(db_path))
    task_id = "phaseD2-asset-task-1"
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

    payload = {
        "task_id": task_id,
        "region_id": "region_x",
        "region_type": "answer_region",
        "image_width": 1200,
        "image_height": 900,
        "bbox": {"x1": 100, "y1": 120, "x2": 500, "y2": 360},
        "teacher_text_feedback": "步骤2漏写单位。",
        "expected_score": 7.5,
        "perception_ir_snapshot": {
            "context_type": "STUDENT_ANSWER",
            "regions": [
                {
                    "target_id": "region_x",
                    "region_type": "answer_region",
                    "bbox": {"x_min": 0.05, "y_min": 0.1, "x_max": 0.6, "y_max": 0.5},
                }
            ],
        },
        "cognitive_ir_snapshot": {
            "status": "SCORED",
            "step_evaluations": [
                {"reference_element_id": "region_x", "is_correct": False, "error_type": "LOGIC"}
            ],
            "overall_feedback": "存在可修正问题",
        },
        "is_integrated_to_dataset": True,
    }

    client = _setup_client(db_path)
    try:
        submit_resp = client.post("/api/v1/annotations/feedback", json=payload)
        assert submit_resp.status_code == 200

        list_resp = client.get(
            "/api/v1/review/annotation-assets"
            "?task_id=phaseD2-asset-task-1&region_id=region_x&region_type=answer_region"
            "&integrated_only=true&sort_by=id&sort_direction=desc&page=1&limit=20"
        )
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        assert list_data["page"] == 1
        assert list_data["limit"] == 20
        assert len(list_data["items"]) == 1
        asset = list_data["items"][0]
        assert asset["task_id"] == task_id
        assert asset["region_id"] == "region_x"
        assert asset["is_integrated_to_dataset"] is True

        detail_resp = client.get(f"/api/v1/review/annotation-assets/{asset['id']}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["id"] == asset["id"]
        assert detail["perception_ir_snapshot"]["regions"][0]["target_id"] == "region_x"
        assert detail["cognitive_ir_snapshot"]["step_evaluations"][0]["reference_element_id"] == "region_x"
    finally:
        app.dependency_overrides.clear()


def test_phaseD2_review_annotation_asset_detail_not_found(tmp_path: Path):
    db_path = str(tmp_path / "phaseD2_annotation_detail_not_found.db")
    asyncio.run(init_db(db_path))

    client = _setup_client(db_path)
    try:
        resp = client.get("/api/v1/review/annotation-assets/999999")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error_code"] == "ANNOTATION_ASSET_NOT_FOUND"
        assert detail["retryable"] is False
    finally:
        app.dependency_overrides.clear()


def test_phaseD2_pending_review_workbench_priority_summary(tmp_path: Path):
    db_path = str(tmp_path / "phaseD2_review_queue_priority.db")
    asyncio.run(init_db(db_path))
    asyncio.run(create_task(db_path, "review-task-a", submitted_count=1))
    asyncio.run(
        update_task_status(
            db_path,
            "review-task-a",
            "COMPLETED",
            grading_status="REJECTED_UNREADABLE",
            review_status="PENDING_REVIEW",
            fallback_reason="PERCEPTION_SHORT_CIRCUIT:UNREADABLE",
        )
    )
    asyncio.run(create_task(db_path, "review-task-b", submitted_count=1))
    asyncio.run(
        update_task_status(
            db_path,
            "review-task-b",
            "COMPLETED",
            grading_status="SCORED",
            review_status="PENDING_REVIEW",
        )
    )
    asyncio.run(
        insert_grading_results(
            db_path,
            records=[
                {
                    "task_id": "review-task-b",
                    "student_id": "stu-001",
                    "total_deduction": 4.0,
                    "is_pass": False,
                    "report_json": {
                        "perception_output": {
                            "elements": [
                                {"element_id": "p0_1", "raw_content": "学生答案片段"}
                            ]
                        },
                        "evaluation_report": {
                            "status": "SCORED",
                            "is_fully_correct": False,
                            "total_score_deduction": 4.0,
                            "step_evaluations": [
                                {
                                    "reference_element_id": "p0_1",
                                    "is_correct": False,
                                    "error_type": "LOGIC",
                                    "correction_suggestion": "请补充关键推导。",
                                }
                            ],
                            "overall_feedback": "存在逻辑问题。",
                            "system_confidence": 0.55,
                            "requires_human_review": True,
                        },
                    },
                }
            ],
        )
    )

    client = _setup_client(db_path)
    try:
        resp = client.get("/api/v1/review/pending-workbench?page=1&limit=20&sort_by=priority")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["pending_task_count"] == 2
        assert data["summary"]["unreadable_task_count"] == 1
        assert data["summary"]["human_review_task_count"] == 1
        assert data["items"][0]["task_id"] == "review-task-a"
        assert data["items"][0]["priority_bucket"] == "UNREADABLE"
        assert data["items"][1]["task_id"] == "review-task-b"
        assert data["items"][1]["priority_bucket"] == "HUMAN_REVIEW"
        assert data["items"][1]["review_target_count"] >= 1
    finally:
        app.dependency_overrides.clear()


def test_phaseD2_review_decision_and_workbench_flow(tmp_path: Path):
    db_path = str(tmp_path / "phaseD2_review_decision_flow.db")
    asyncio.run(init_db(db_path))
    task_id = "phaseD2-review-task"
    asyncio.run(create_task(db_path, task_id, submitted_count=1))
    asyncio.run(
        update_task_status(
            db_path,
            task_id,
            "COMPLETED",
            grading_status="SCORED",
            review_status="PENDING_REVIEW",
        )
    )
    asyncio.run(
        insert_grading_results(
            db_path,
            records=[
                {
                    "task_id": task_id,
                    "student_id": "stu-review-1",
                    "total_deduction": 2.5,
                    "is_pass": False,
                    "report_json": {
                        "perception_output": {
                            "elements": [
                                {"element_id": "p0_1", "raw_content": "关键作答片段"}
                            ]
                        },
                        "perception_ir_snapshot": {
                            "elements": [
                                {"element_id": "p0_1", "raw_content": "关键作答片段"}
                            ]
                        },
                        "cognitive_ir_snapshot": {
                            "status": "SCORED",
                            "step_evaluations": [
                                {
                                    "reference_element_id": "p0_1",
                                    "is_correct": False,
                                    "error_type": "CONCEPTUAL",
                                    "correction_suggestion": "请重新判断受力方向。",
                                }
                            ],
                            "overall_feedback": "存在概念偏差。",
                            "system_confidence": 0.61,
                            "requires_human_review": True,
                        },
                        "evaluation_report": {
                            "status": "SCORED",
                            "is_fully_correct": False,
                            "total_score_deduction": 2.5,
                            "step_evaluations": [
                                {
                                    "reference_element_id": "p0_1",
                                    "is_correct": False,
                                    "error_type": "CONCEPTUAL",
                                    "correction_suggestion": "请重新判断受力方向。",
                                }
                            ],
                            "overall_feedback": "存在概念偏差。",
                            "system_confidence": 0.61,
                            "requires_human_review": True,
                        },
                    },
                }
            ],
        )
    )

    client = _setup_client(db_path)
    try:
        decision_resp = client.post(
            "/api/v1/review/decisions",
            json={
                "task_id": task_id,
                "sample_id": "stu-review-1",
                "student_id": "stu-review-1",
                "decision": "ADJUST_SCORE",
                "final_score": 7.5,
                "teacher_comment": "教师改判：步骤方向判断错误，但后续思路部分合理。",
                "include_in_dataset": True,
            },
        )
        assert decision_resp.status_code == 200
        assert decision_resp.json()["decision"] == "ADJUST_SCORE"

        list_resp = client.get(f"/api/v1/review/decisions?task_id={task_id}&page=1&limit=20")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["items"]) == 1

        workbench_resp = client.get(f"/api/v1/review/workbench/{task_id}")
        assert workbench_resp.status_code == 200
        workbench = workbench_resp.json()
        assert workbench["review_status"] == "PENDING_REVIEW"
        assert workbench["risk_summary"]["reviewed_decision_count"] == 1
        assert workbench["samples"][0]["teacher_decision"]["decision"] == "ADJUST_SCORE"

        mark_resp = client.post(
            f"/api/v1/review/tasks/{task_id}/status",
            json={"review_status": "REVIEWED"},
        )
        assert mark_resp.status_code == 200
        assert mark_resp.json()["review_status"] == "REVIEWED"
    finally:
        app.dependency_overrides.clear()
