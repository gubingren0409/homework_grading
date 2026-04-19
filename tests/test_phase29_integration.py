"""
Phase 29: Integration Tests with Fakeredis

Tests Celery serialization boundaries and worker execution flow without
requiring a real Redis instance.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fakeredis import FakeStrictRedis
from fastapi import Request, Response

from src.worker.main import app as celery_app, grade_homework_task
from src.db.client import create_task, get_task, init_db
from src.core.storage_adapter import storage


@pytest.fixture
def fake_redis():
    """Provide in-memory Redis substitute."""
    return FakeStrictRedis(decode_responses=True)


@pytest.fixture
def test_db_path(tmp_path):
    """Provide temporary test database."""
    db_path = str(tmp_path / "test_grading.db")
    asyncio.run(init_db(db_path))
    return db_path


@pytest.mark.asyncio
async def test_serialization_boundary_bytes_to_int_list(fake_redis, test_db_path):
    """
    Phase 29 Critical Test: Verify bytes → int list serialization prevents EncodeError.
    
    Scenario: API gateway serializes uploaded file bytes to JSON-compatible int arrays.
    Expected: Celery can successfully serialize/deserialize the payload.
    """
    task_id = "test-serialize-001"
    
    # Simulate API gateway payload construction
    fake_file_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"  # PNG header bytes
    file_ref = storage.store_file(task_id, fake_file_content, "test.png")
    payload = storage.prepare_payload([file_ref])
    
    # Verify JSON serializability for queue payload
    try:
        json.dumps(payload)  # Should succeed
    except TypeError:
        pytest.fail("Serialization check failed: payload contains non-JSON types")
    
    # Verify Celery can accept this payload
    await create_task(test_db_path, task_id)
    
    # Mock workflow execution to isolate serialization test
    with patch("src.worker.main._build_workflow") as mock_workflow_factory:
        from src.schemas.cognitive_ir import EvaluationReport
        
        # Create real Pydantic object (not AsyncMock)
        mock_report = EvaluationReport(
            is_fully_correct=True,
            total_score_deduction=0.0,
            step_evaluations=[],
            overall_feedback="Test feedback",
            system_confidence=1.0,
            requires_human_review=False,
        )
        
        mock_workflow = AsyncMock()
        mock_workflow.run_pipeline = AsyncMock(return_value=mock_report)
        mock_workflow_factory.return_value = mock_workflow
        
        # Execute task synchronously (bypasses broker but tests serialization)
        result = grade_homework_task(task_id, payload, test_db_path)
        
        assert result["status"] == "success"
        assert mock_workflow.run_pipeline.called


@pytest.mark.asyncio
async def test_zombie_task_detection_timeout(test_db_path):
    """
    Phase 29 Critical Test: Zombie sweeper marks stuck PROCESSING tasks as FAILED.
    
    Scenario: Worker crashes mid-task, leaving status stuck in PROCESSING.
    Expected: Zombie sweeper detects timeout and marks as FAILED.
    """
    from scripts.zombie_sweeper import sweep_zombie_tasks
    
    task_id = "zombie-task-001"
    await create_task(test_db_path, task_id)
    
    # Simulate worker crash: manually set PROCESSING without completion
    async with (await import_aiosqlite()).connect(test_db_path) as db:
        await db.execute(
            "UPDATE tasks SET status = 'PROCESSING', updated_at = datetime('now', '-15 minutes') WHERE task_id = ?",
            (task_id,)
        )
        await db.commit()
    
    # Run sweeper with 10-minute threshold
    zombie_count = await sweep_zombie_tasks(test_db_path, timeout_seconds=600, dry_run=False)
    
    assert zombie_count == 1
    
    # Verify task marked as FAILED
    task = await get_task(test_db_path, task_id)
    assert task["status"] == "FAILED"
    assert "Worker timeout" in task["error_message"]


async def import_aiosqlite():
    """Dynamic import helper for test isolation."""
    import aiosqlite
    return aiosqlite


@pytest.mark.asyncio
async def test_acks_late_configuration():
    """
    Phase 29 Contract Test: Verify worker has acks_late=True to prevent message loss.
    
    Critical: Tasks should only be removed from broker queue AFTER completion.
    """
    assert celery_app.conf.task_acks_late is True, "Worker MUST have acks_late=True"


@pytest.mark.asyncio
async def test_polling_endpoint_contract_pending_status(test_db_path):
    """
    Phase 29 Contract Test: Polling endpoint must return progress/ETA for PENDING tasks.
    
    This ensures frontend can display meaningful waiting indicators.
    """
    from src.api.routers.grade import get_job_status_and_results
    task_id = "test-polling-001"
    await create_task(test_db_path, task_id)

    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/grade/{task_id}",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    response = Response()
    result = await get_job_status_and_results(request, response, task_id, test_db_path)

    assert response.status_code == 200
    assert response.headers.get("ETag")
    assert result.status == "PENDING"
    assert result.progress is not None, "PENDING tasks must include progress field"
    assert result.eta_seconds is not None, "PENDING tasks must include ETA"


@pytest.mark.asyncio
async def test_polling_endpoint_sanitizes_internal_errors(test_db_path):
    """
    Phase 29 Security Test: Polling endpoint must NOT leak internal stack traces.
    
    Expected: Raw Python exceptions replaced with sanitized error_code.
    """
    from src.api.routers.grade import get_job_status_and_results
    task_id = "test-error-001"
    await create_task(test_db_path, task_id)
    
    # Simulate internal error with stack trace
    raw_error = """Traceback (most recent call last):
  File "/app/worker.py", line 42
    raise ValueError("secret API key exposed")
ValueError: secret API key exposed"""
    
    async with (await import_aiosqlite()).connect(test_db_path) as db:
        await db.execute(
            "UPDATE tasks SET status = 'FAILED', error_message = ? WHERE task_id = ?",
            (raw_error, task_id)
        )
        await db.commit()
    
    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/grade/{task_id}",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    response = Response()
    result = await get_job_status_and_results(request, response, task_id, test_db_path)

    assert result.error_code == "INTERNAL_ERROR"
    assert "Traceback" not in result.error_message, "Must NOT leak stack traces"
    assert "secret" not in result.error_message.lower(), "Must NOT leak secrets"
