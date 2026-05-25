"""
Tests for Single submission grading API endpoints.

This module tests the single submission grading routes:
- POST /grade/submit - Submit single grading job
- GET /grade/{task_id} - Query task status
- GET /grade/{task_id}/report - Get grading report
- GET /grade/{task_id}/insights - Get task insights
- POST /grade/{task_id}/cancel - Cancel task
"""

import asyncio
import io
from unittest.mock import Mock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.api.dependencies import get_db_path
from src.core.storage_adapter import storage
from src.db.client import (
    create_task,
    init_db,
    update_task_status,
)
from src.db.dao.rubrics import save_rubric, get_rubric
from src.db.dao.results import save_grading_result
from src.db.dao._adapter_base import set_test_db_path
from src.main import app
from src.schemas.cognitive_ir import EvaluationReport

client = TestClient(app)


def _make_test_image_bytes() -> bytes:
    """Create a test image."""
    image = Image.new("RGB", (64, 64), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def mock_celery_dispatch(monkeypatch):
    """Mock Celery task dispatch."""
    mock_result = Mock()
    mock_result.id = "celery-task-123"

    def mock_dispatch(*args, **kwargs):
        return "celery-task-123", "celery_queue"

    monkeypatch.setattr(
        "src.api.routers.grade_modules.single._dispatch_grading_task",
        mock_dispatch
    )
    return mock_dispatch


def test_submit_single_grade_endpoint_creates_task(tmp_path, mock_celery_dispatch):
    """Test that submitting a single grading job creates a task."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            # Submit grading job
            image_bytes = _make_test_image_bytes()
            response = client.post(
                "/api/v1/grade/submit",
                files={"files": ("test.png", image_bytes, "image/png")},
                data={"student_id": "student-123"},
            )

            assert response.status_code == 202
            payload = response.json()
            assert payload["status"] == "PENDING"
            assert payload["mode"] == "single_submission"
            assert payload["submitted_count"] == 1
            assert "task_id" in payload
            assert "status_endpoint" in payload
            assert "stream_endpoint" in payload
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_submit_single_grade_endpoint_with_rubric(tmp_path, mock_celery_dispatch):
    """Test submitting with a rubric."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a rubric
        rubric_json = {
            "question_id": "q1",
            "steps": [{"step_id": "s1", "description": "Step 1", "points": 10}],
        }
        asyncio.run(save_rubric("rubric-1", "q1", rubric_json))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            # Submit with rubric
            image_bytes = _make_test_image_bytes()
            response = client.post(
                "/api/v1/grade/submit",
                files={"files": ("test.png", image_bytes, "image/png")},
                data={"student_id": "student-123", "rubric_id": "rubric-1"},
            )

            assert response.status_code == 202
            payload = response.json()
            assert payload["rubric_id"] == "rubric-1"
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_submit_single_grade_endpoint_rejects_invalid_rubric(tmp_path, mock_celery_dispatch):
    """Test that invalid rubric ID is rejected."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            # Submit with invalid rubric
            image_bytes = _make_test_image_bytes()
            response = client.post(
                "/api/v1/grade/submit",
                files={"files": ("test.png", image_bytes, "image/png")},
                data={"student_id": "student-123", "rubric_id": "invalid-rubric"},
            )

            assert response.status_code == 404
            payload = response.json()
            assert payload["detail"]["error_code"] == "RUBRIC_NOT_FOUND"
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_get_task_status_endpoint_returns_task_info(tmp_path):
    """Test querying task status."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a task
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="test-teacher"))
        asyncio.run(update_task_status("task-123", "PROCESSING"))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            response = client.get("/api/v1/grade/task-123")

            assert response.status_code == 200
            payload = response.json()
            assert payload["task_id"] == "task-123"
            assert payload["status"] == "PROCESSING"
            assert payload["submitted_count"] == 1
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_get_task_status_endpoint_rejects_unauthorized_access(tmp_path):
    """Test that users cannot access other teachers' tasks."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a task for another teacher
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="other-teacher"))

        # Mock authentication as different teacher
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            response = client.get("/api/v1/grade/task-123")

            # Should return 404 or 403
            assert response.status_code in [403, 404]
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_get_task_report_endpoint_returns_grading_results(tmp_path):
    """Test getting grading report."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a task and result
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="test-teacher"))
        asyncio.run(update_task_status("task-123", "COMPLETED", grading_status="SCORED"))

        # Save grading result
        report = EvaluationReport(
            status="SCORED",
            is_fully_correct=False,
            total_score_deduction=5.0,
            step_evaluations=[],
            overall_feedback="Good work",
            system_confidence=0.9,
            requires_human_review=False,
        )
        asyncio.run(
            save_grading_result(
                task_id="task-123",
                student_id="student-123",
                report=report,
            )
        )

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            response = client.get("/api/v1/grade/task-123/report")

            assert response.status_code == 200
            payload = response.json()
            assert payload["task_id"] == "task-123"
            assert payload["task_status"] == "COMPLETED"
            assert len(payload["cards"]) == 1
            assert payload["cards"][0]["student_id"] == "student-123"
            assert payload["cards"][0]["total_deduction"] == 5.0
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_get_task_insights_endpoint_returns_statistics(tmp_path):
    """Test getting task insights and statistics."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a task and multiple results
        asyncio.run(create_task("task-123", submitted_count=2, teacher_id="test-teacher"))
        asyncio.run(update_task_status("task-123", "COMPLETED", grading_status="SCORED"))

        # Save grading results
        for i in range(2):
            report = EvaluationReport(
                status="SCORED",
                is_fully_correct=i == 0,
                total_score_deduction=0.0 if i == 0 else 5.0,
                step_evaluations=[],
                overall_feedback="Good work",
                system_confidence=0.9,
                requires_human_review=i == 1,
            )
            asyncio.run(
                save_grading_result(
                    task_id="task-123",
                    student_id=f"student-{i}",
                    report=report,
                )
            )

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            response = client.get("/api/v1/grade/task-123/insights")

            assert response.status_code == 200
            payload = response.json()
            assert payload["task_id"] == "task-123"
            assert payload["task_status"] == "COMPLETED"
            # Should have statistics about error types, review buckets, etc.
            assert "error_type_counts" in payload
            assert "review_bucket_counts" in payload
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_cancel_task_endpoint_cancels_pending_task(tmp_path):
    """Test canceling a pending task."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a pending task
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="test-teacher"))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        # Mock Celery revoke
        with patch("src.api.route_helpers.remove_task_from_celery_queue") as mock_revoke:
            mock_revoke.return_value = None

            try:
                response = client.post("/api/v1/grade/task-123/cancel")

                assert response.status_code == 200
                payload = response.json()
                assert payload["task_id"] == "task-123"
                assert payload["cancelled"] == True
                assert payload["previous_status"] == "PENDING"
            finally:
                app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_cancel_task_endpoint_rejects_completed_task(tmp_path):
    """Test that completed tasks cannot be cancelled."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a completed task
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="test-teacher"))
        asyncio.run(update_task_status("task-123", "COMPLETED"))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            response = client.post("/api/v1/grade/task-123/cancel")

            # API returns 200 with cancelled=False for completed tasks
            assert response.status_code == 200
            payload = response.json()
            assert payload["cancelled"] == False
            assert "已处于终态" in payload["message"]
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)
