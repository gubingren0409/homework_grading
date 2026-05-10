import asyncio
import io

from fastapi.testclient import TestClient
from PIL import Image

from src.api.auth import create_access_token
from src.api.dependencies import get_db_path
from src.core.config import settings
from src.core.storage_adapter import storage
from src.db.client import (
    create_golden_annotation_asset,
    create_task,
    fetch_results_by_task,
    init_db,
    save_grading_result,
    update_task_status,
)
from src.main import app
from src.schemas.cognitive_ir import EvaluationReport


client = TestClient(app)


def _make_test_image_bytes() -> bytes:
    image = Image.new("RGB", (64, 64), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _headers(teacher_id: str, teacher_name: str) -> dict[str, str]:
    token = create_access_token(teacher_id, teacher_name)
    return {"Authorization": f"Bearer {token}"}


async def _seed_scored_task(
    db_path: str,
    *,
    task_id: str,
    teacher_id: str,
    student_id: str,
) -> tuple[int, int]:
    await create_task(db_path, task_id, submitted_count=1, teacher_id=teacher_id)
    file_ref = storage.store_file(task_id, _make_test_image_bytes(), f"{student_id}.png")
    await save_grading_result(
        db_path,
        task_id,
        student_id,
        EvaluationReport(
            status="SCORED",
            is_fully_correct=False,
            total_score_deduction=1.0,
            step_evaluations=[],
            overall_feedback="needs review",
            system_confidence=0.9,
            requires_human_review=True,
        ),
        report_payload_extras={
            "input_file_refs": [file_ref],
            "input_filenames": [f"{student_id}.png"],
        },
    )
    await update_task_status(
        db_path,
        task_id,
        "COMPLETED",
        grading_status="SCORED",
        review_status="PENDING_REVIEW",
    )
    rows = await fetch_results_by_task(db_path, task_id)
    result_id = int(rows[0]["id"])
    await create_golden_annotation_asset(
        db_path,
        trace_id=f"trace-{task_id}",
        task_id=task_id,
        region_id=f"region-{task_id}",
        region_type="answer_region",
        image_width=64,
        image_height=64,
        bbox_coordinates=[0.1, 0.1, 0.8, 0.8],
        perception_ir_snapshot={"task_id": task_id},
        cognitive_ir_snapshot={"task_id": task_id},
        teacher_text_feedback="feedback",
        expected_score=3.0,
        is_integrated_to_dataset=False,
    )
    asset_rows = await fetch_results_by_task(db_path, task_id)
    del asset_rows
    return result_id, 1


def test_teacher_auth_isolates_grade_and_review_data(tmp_path, monkeypatch):
    db_path = str(tmp_path / "teacher_auth_isolation.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_secret_key", "test-secret-key-with-32-bytes-minimum")

    try:
        result_id_a, _ = asyncio.run(
            _seed_scored_task(
                db_path,
                task_id="task-teacher-a",
                teacher_id="teacher-a",
                student_id="student-a",
            )
        )
        result_id_b, _ = asyncio.run(
            _seed_scored_task(
                db_path,
                task_id="task-teacher-b",
                teacher_id="teacher-b",
                student_id="student-b",
            )
        )

        asyncio.run(
            create_golden_annotation_asset(
                db_path,
                trace_id="asset-a",
                task_id="task-teacher-a",
                region_id="asset-region-a",
                region_type="answer_region",
                image_width=64,
                image_height=64,
                bbox_coordinates=[0.1, 0.1, 0.8, 0.8],
                perception_ir_snapshot={"owner": "teacher-a"},
                cognitive_ir_snapshot={"owner": "teacher-a"},
                teacher_text_feedback="feedback-a",
                expected_score=3.0,
                is_integrated_to_dataset=False,
            )
        )
        asyncio.run(
            create_golden_annotation_asset(
                db_path,
                trace_id="asset-b",
                task_id="task-teacher-b",
                region_id="asset-region-b",
                region_type="answer_region",
                image_width=64,
                image_height=64,
                bbox_coordinates=[0.1, 0.1, 0.8, 0.8],
                perception_ir_snapshot={"owner": "teacher-b"},
                cognitive_ir_snapshot={"owner": "teacher-b"},
                teacher_text_feedback="feedback-b",
                expected_score=3.0,
                is_integrated_to_dataset=False,
            )
        )

        teacher_a = _headers("teacher-a", "Teacher A")
        teacher_b = _headers("teacher-b", "Teacher B")

        own_status = client.get("/api/v1/grade/task-teacher-a", headers=teacher_a)
        assert own_status.status_code == 200

        own_results = client.get("/api/v1/results", headers=teacher_a)
        assert own_results.status_code == 200
        assert len(own_results.json()) == 1
        assert own_results.json()[0]["student_id"] == "student-a"

        own_task_results = client.get("/api/v1/results?task_id=task-teacher-a", headers=teacher_a)
        assert own_task_results.status_code == 200
        assert len(own_task_results.json()) == 1

        own_report = client.get("/api/v1/grade/task-teacher-a/report", headers=teacher_a)
        assert own_report.status_code == 200
        assert len(own_report.json()["cards"]) == 1

        own_workbench = client.get("/api/v1/review/workbench/task-teacher-a", headers=teacher_a)
        assert own_workbench.status_code == 200
        assert own_workbench.json()["task_id"] == "task-teacher-a"

        own_pending = client.get("/api/v1/tasks/pending-review?page=1&limit=20", headers=teacher_a)
        assert own_pending.status_code == 200
        assert [item["task_id"] for item in own_pending.json()] == ["task-teacher-a"]

        own_assets = client.get("/api/v1/annotations/assets?page=1&limit=20", headers=teacher_a)
        assert own_assets.status_code == 200
        own_asset_items = own_assets.json()
        assert len(own_asset_items) == 2
        assert all(item["task_id"] == "task-teacher-a" for item in own_asset_items)
        own_asset_detail = client.get(
            f"/api/v1/review/annotation-assets/{own_asset_items[0]['id']}",
            headers=teacher_a,
        )
        assert own_asset_detail.status_code == 200

        own_input = client.get(f"/api/v1/results/{result_id_a}/inputs/0", headers=teacher_a)
        assert own_input.status_code == 200
        assert own_input.headers["content-type"] == "image/png"

        blocked_paths = [
            "/api/v1/grade/task-teacher-a",
            "/api/v1/results?task_id=task-teacher-a",
            "/api/v1/grade/task-teacher-a/report",
            "/api/v1/grade/task-teacher-a/insights",
            "/api/v1/review/workbench/task-teacher-a",
            "/api/v1/review/decisions?task_id=task-teacher-a&page=1&limit=20",
            "/api/v1/annotations/assets?task_id=task-teacher-a&page=1&limit=20",
        ]
        for path in blocked_paths:
            response = client.get(path, headers=teacher_b)
            assert response.status_code == 404

        blocked_input = client.get(f"/api/v1/results/{result_id_a}/inputs/0", headers=teacher_b)
        assert blocked_input.status_code == 404
        blocked_asset_detail = client.get(
            f"/api/v1/review/annotation-assets/{own_asset_items[0]['id']}",
            headers=teacher_b,
        )
        assert blocked_asset_detail.status_code == 404

        blocked_cancel = client.post("/api/v1/grade/task-teacher-a/cancel", headers=teacher_b)
        assert blocked_cancel.status_code == 404

        teacher_b_results = client.get("/api/v1/results", headers=teacher_b)
        assert teacher_b_results.status_code == 200
        assert len(teacher_b_results.json()) == 1
        assert teacher_b_results.json()[0]["student_id"] == "student-b"

        assert result_id_b > 0
    finally:
        app.dependency_overrides.clear()
