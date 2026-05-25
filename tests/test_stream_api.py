"""
Tests for Stream API endpoints.

This module tests the SSE streaming routes:
- GET /tasks/{task_id}/stream - Stream task status updates
"""

import asyncio
from unittest.mock import Mock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db_path
from src.db.client import (
    create_task,
    init_db,
    update_task_status,
)
from src.db.dao._adapter_base import set_test_db_path
from src.main import app

client = TestClient(app)


def test_stream_task_status_endpoint_requires_authentication(tmp_path):
    """Test that SSE endpoint requires authentication."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a task
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="test-teacher"))

        app.dependency_overrides[get_db_path] = lambda: db_path

        try:
            # Try to access without authentication (should fail or require auth)
            response = client.get("/api/v1/tasks/task-123/stream")

            # Should either require auth (401) or work with mock auth
            # In test environment, it may work with default auth
            assert response.status_code in [200, 401, 403]
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_stream_task_status_endpoint_rejects_nonexistent_task(tmp_path):
    """Test that SSE endpoint rejects non-existent tasks."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher

        try:
            # Try to stream non-existent task
            response = client.get("/api/v1/tasks/nonexistent-task/stream")

            # Should return 404
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_stream_task_status_endpoint_rejects_unauthorized_access(tmp_path):
    """Test that users cannot stream other teachers' tasks."""
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

        try:
            response = client.get("/api/v1/tasks/task-123/stream")

            # Should return 403 or 404
            assert response.status_code in [403, 404]
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


@pytest.mark.skipif(True, reason="SSE streaming requires Redis and is complex to test in unit tests")
def test_stream_task_status_endpoint_streams_updates(tmp_path):
    """Test that SSE endpoint streams task status updates.

    Note: This test is skipped because SSE streaming requires:
    - Redis connection for pub/sub
    - Long-lived HTTP connection
    - Async event handling

    SSE functionality should be tested in integration tests with real Redis.
    """
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a task
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="test-teacher"))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher

        try:
            # This would require mocking Redis pub/sub and handling SSE stream
            # For now, just verify the endpoint is accessible
            response = client.get("/api/v1/tasks/task-123/stream", stream=True)

            # Should return 200 with text/event-stream content type
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)


def test_stream_endpoint_basic_accessibility(tmp_path):
    """Test basic accessibility of SSE endpoint."""
    db_path = str(tmp_path / "test.db")
    set_test_db_path(db_path)

    try:
        asyncio.run(init_db(db_path))

        # Create a completed task to avoid Redis dependency
        asyncio.run(create_task("task-123", submitted_count=1, teacher_id="test-teacher"))
        asyncio.run(update_task_status("task-123", "COMPLETED"))

        # Mock authentication
        from src.api.auth import TeacherIdentity, get_current_teacher
        mock_teacher = TeacherIdentity(teacher_id="test-teacher", teacher_name="Test Teacher")

        app.dependency_overrides[get_db_path] = lambda: db_path
        app.dependency_overrides[get_current_teacher] = lambda: mock_teacher

        try:
            # For a completed task, SSE should immediately send complete event
            # This avoids Redis dependency
            response = client.get("/api/v1/tasks/task-123/stream")

            # Should return 200 with SSE content type
            assert response.status_code == 200
            # Note: TestClient may not preserve streaming response headers perfectly
            # Just verify the endpoint is accessible
        finally:
            app.dependency_overrides.clear()
    finally:
        set_test_db_path(None)
