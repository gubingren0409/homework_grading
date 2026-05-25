"""
Tests for Batch grading API endpoints.

This module tests the batch grading routes:
- POST /grade/submit-batch - Submit batch grading job
- POST /grade/submit-batch-with-reference - Submit batch with reference
- GET /grade-batch/{task_id} - Query batch job status
"""

import asyncio
import io
from unittest.mock import Mock, patch

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
from src.db.dao.rubrics import save_rubric
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
        "src.api.routers.grade_modules.batch._dispatch_grading_task",
        mock_dispatch
    )
    return mock_dispatch


def test_submit_batch_grading_endpoint_creates_task(tmp_path, mock_celery_dispatch):
    """Test that submitting a batch grading job creates a task."""
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
            # Submit batch grading job with 3 files
            image_bytes = _make_test_image_bytes()
            files = [
                ("files", ("student1.png", image_bytes, "image/png")),
                ("files", ("student2.png", image_bytes, "image/png")),
                ("files", ("student3.png", image_bytes, "image/png")),
            ]
            response = client.post(
                "/api/v1/grade/submit-batch",
                files=files,
            )

            assert response.status_code == 202
            payload = response.json()
            assert payload["status"] == "PENDING"
            assert payload["mode"] == "batch_single_page"
            assert payload["submitted_count"] == 3
            assert "task_id" in payload
            assert "status_endpoint" in payload
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_submit_batch_grading_endpoint_with_rubric(tmp_path, mock_celery_dispatch):
    """Test submitting batch with a rubric."""
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
            files = [
                ("files", ("student1.png", image_bytes, "image/png")),
                ("files", ("student2.png", image_bytes, "image/png")),
            ]
            response = client.post(
                "/api/v1/grade/submit-batch",
                files=files,
                data={"rubric_id": "rubric-1"},
            )

            assert response.status_code == 202
            payload = response.json()
            assert payload["rubric_id"] == "rubric-1"
            assert payload["submitted_count"] == 2
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_submit_batch_grading_endpoint_rejects_empty_files(tmp_path):
    """Test that empty file list is rejected."""
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
            # Submit with no files
            response = client.post(
                "/api/v1/grade/submit-batch",
                files=[],
            )

            # FastAPI returns 422 for missing required field
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_submit_batch_with_reference_endpoint_creates_task(tmp_path, mock_celery_dispatch):
    """Test submitting batch with reference files."""
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
            # Submit with reference and student files
            image_bytes = _make_test_image_bytes()
            response = client.post(
                "/api/v1/grade/submit-batch-with-reference",
                files=[
                    ("reference_files", ("reference.png", image_bytes, "image/png")),
                    ("files", ("student1.png", image_bytes, "image/png")),
                    ("files", ("student2.png", image_bytes, "image/png")),
                ],
            )

            assert response.status_code == 202
            payload = response.json()
            assert payload["status"] == "PENDING"
            assert payload["mode"] == "batch_single_page"
            assert payload["submitted_count"] == 2  # Only student files count
            assert "task_id" in payload
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_submit_batch_with_reference_endpoint_rejects_missing_reference(tmp_path):
    """Test that missing reference files are rejected."""
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
            # Submit without reference files
            image_bytes = _make_test_image_bytes()
            response = client.post(
                "/api/v1/grade/submit-batch-with-reference",
                files=[
                    ("files", ("student1.png", image_bytes, "image/png")),
                ],
            )

            # FastAPI returns 422 for missing required field
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_get_batch_job_status_endpoint_returns_task_info(tmp_path):
    """Test querying batch job status."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a batch task
        asyncio.run(create_task("task-123", submitted_count=3, teacher_id="test-teacher"))
        asyncio.run(update_task_status("task-123", "PROCESSING"))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            response = client.get("/api/v1/grade-batch/task-123")

            assert response.status_code == 200
            payload = response.json()
            assert payload["task_id"] == "task-123"
            assert payload["status"] == "PROCESSING"
            assert payload["submitted_count"] == 3
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_get_batch_job_status_endpoint_rejects_unauthorized_access(tmp_path):
    """Test that users cannot access other teachers' batch tasks."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a task for another teacher
        asyncio.run(create_task("task-123", submitted_count=3, teacher_id="other-teacher"))

        # Mock authentication as different teacher
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher
        client.app.state.limiter.reset()

        try:
            response = client.get("/api/v1/grade-batch/task-123")

            # Should return 404 or 403
            assert response.status_code in [403, 404]
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)
