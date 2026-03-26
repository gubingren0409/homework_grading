"""
Phase 30: Worker Deserialization Type Validation Tests

Ensures worker receives native Python types (dict/list), not stringified versions.
Uses Base64 encoding for efficient binary transport (Phase 30.1).
"""
import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.core.serialization import (
    prepare_file_payload,
    serialize_for_celery,
    CeleryJSONEncoder,
)
from src.worker.main import grade_homework_task
from src.db.client import init_db, create_task


@pytest.fixture
def test_db_path(tmp_path):
    """Provide temporary test database."""
    db_path = str(tmp_path / "test_grading.db")
    asyncio.run(init_db(db_path))
    return db_path


def test_prepare_file_payload_returns_dict_not_tuple():
    """
    Phase 30.1: Verify payload is dict with Base64-encoded content.
    
    Ensures worker receives structured data with efficient Base64 encoding.
    """
    content = b"\x89PNG\r\n\x1a\n"
    filename = "test.png"
    
    payload = prepare_file_payload(content, filename)
    
    assert isinstance(payload, dict), "Payload must be dict"
    assert "content" in payload, "Must have 'content' key"
    assert "filename" in payload, "Must have 'filename' key"
    assert isinstance(payload["content"], str), "Content must be Base64 string"
    assert isinstance(payload["filename"], str), "Filename must be str"
    
    # Verify Base64 encoding (not int list)
    decoded = base64.b64decode(payload["content"])
    assert decoded == content, "Base64 round-trip must preserve bytes"


def test_celery_encoder_handles_path_objects():
    """
    Phase 30: Verify custom encoder converts Path to str without str() coercion.
    """
    test_path = Path("/tmp/test.txt")
    payload = {"path": test_path, "name": "test"}
    
    # Serialize with custom encoder
    serialized = json.dumps(payload, cls=CeleryJSONEncoder)
    deserialized = json.loads(serialized)
    
    # On Windows, paths use backslashes - normalize for comparison
    expected_path = str(test_path)
    assert deserialized["path"] == expected_path
    assert isinstance(deserialized["path"], str)
    assert deserialized["name"] == "test"


def test_serialize_for_celery_preserves_nested_structures():
    """
    Phase 30 Critical Test: Ensure nested dicts/lists remain native types.
    
    ANTI-PATTERN: str({"key": "value"}) -> "{'key': 'value'}" (string literal)
    CORRECT: {"key": "value"} -> {"key": "value"} (dict)
    """
    payload = {
        "files": [
            {"name": "file1.txt", "size": 100},
            {"name": "file2.txt", "size": 200},
        ],
        "metadata": {"user": "test", "timestamp": 123456}
    }
    
    serialized = serialize_for_celery(payload)
    
    # Verify types are preserved
    assert isinstance(serialized, dict)
    assert isinstance(serialized["files"], list)
    assert isinstance(serialized["files"][0], dict)
    assert isinstance(serialized["metadata"], dict)
    
    # Verify it's not stringified
    assert serialized != str(payload), "Must NOT be stringified"


@pytest.mark.asyncio
async def test_worker_receives_dict_not_string(test_db_path):
    """
    Phase 30 Critical Test: Worker deserialization type assertion.
    
    Validates worker receives dict with native types, not string representations.
    """
    task_id = "test-worker-types-001"
    await create_task(test_db_path, task_id)
    
    # Prepare payload using Phase 30 serialization
    fake_content = b"test content"
    file_payload = prepare_file_payload(fake_content, "test.txt")
    files_data = [file_payload]
    
    # Mock workflow to intercept deserialized data
    with patch("src.worker.main._build_workflow") as mock_workflow_factory:
        from src.schemas.cognitive_ir import EvaluationReport
        
        mock_report = EvaluationReport(
            is_fully_correct=True,
            total_score_deduction=0.0,
            step_evaluations=[],
            overall_feedback="Test",
            system_confidence=1.0,
            requires_human_review=False,
        )
        
        # Capture deserialized files_data received by worker
        captured_files = []
        
        async def mock_pipeline(files):
            captured_files.extend(files)
            return mock_report
        
        mock_workflow = AsyncMock()
        mock_workflow.run_pipeline = mock_pipeline
        mock_workflow_factory.return_value = mock_workflow
        
        # Execute task in a separate thread to avoid event loop collision
        import concurrent.futures
        
        def run_task():
            return grade_homework_task(task_id, files_data, test_db_path)
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_task)
            result = future.result(timeout=10)
        
        assert result["status"] == "success"
        
        # CRITICAL ASSERTION: Verify worker received bytes + str (not string representation)
        assert len(captured_files) == 1
        file_bytes, filename = captured_files[0]
        
        assert isinstance(file_bytes, bytes), "Worker must receive bytes (not string)"
        assert isinstance(filename, str), "Filename must be str"
        assert file_bytes == b"test content", "Content must match original bytes"


def test_no_destructive_str_coercion():
    """
    Phase 30: Regression test - ensure no str() coercion on complex types.
    """
    complex_payload = {
        "list": [1, 2, 3],
        "dict": {"key": "value"},
        "path": Path("/tmp/test"),
    }
    
    serialized = serialize_for_celery(complex_payload)
    
    # Lists and dicts must remain as-is
    assert serialized["list"] == [1, 2, 3]
    assert serialized["dict"] == {"key": "value"}
    
    # Path should be converted to string (but structure preserved)
    expected_path = str(Path("/tmp/test"))
    assert serialized["path"] == expected_path
    
    # CRITICAL: Ensure it's not a stringified representation
    # If dict was str() coerced, it would be "{'key': 'value'}" (string with quotes)
    dict_str = json.dumps(serialized["dict"])
    assert "{\"key\": \"value\"}" in dict_str or '{"key":"value"}' in dict_str, \
        "Dict must serialize as JSON, not Python str() representation"
