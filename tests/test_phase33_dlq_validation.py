"""
Phase 33: Dead Letter Queue (DLQ) Integration Tests

Validates that poison messages (permanently failed tasks) are correctly
routed to DLQ in Redis Broker environment (not RabbitMQ).

Critical Validation:
- ✅ Explicit routing via _route_to_dlq() after max retries
- ✅ DLQ entry contains full task context (payload, error, timestamp)
- ✅ DLQ tools (inspect, replay, purge) work correctly
- ❌ NOT relying on task_reject_on_worker_lost (doesn't work with Redis)
"""
import pytest
import json
import redis
from unittest.mock import Mock, patch, AsyncMock

from src.worker.main import _route_to_dlq, DLQ_QUEUE_NAME
from src.worker.dlq import (
    get_dlq_stats,
    inspect_dlq_task,
    replay_dlq_task,
    purge_dlq,
    list_dlq_tasks,
)
from src.core.config import settings


@pytest.fixture
def redis_client():
    """Redis client for DLQ testing."""
    client = redis.from_url(settings.redis_url)
    # Clear DLQ before each test
    client.delete(DLQ_QUEUE_NAME)
    yield client
    # Cleanup after test
    client.delete(DLQ_QUEUE_NAME)
    client.close()


def test_route_to_dlq_explicit_push(redis_client):
    """
    Test: _route_to_dlq() explicitly pushes to Redis DLQ.
    
    This is the correct implementation for Redis Broker.
    Celery's task_reject_on_worker_lost does NOT work with Redis.
    """
    task_id = "failed-task-123"
    payload = {"file_refs": ["s3://bucket/task/file.png"]}
    db_path = "/tmp/test.db"
    error = "Unhandled exception: NoneType has no attribute 'foo'"
    
    # Route to DLQ (explicit Redis push)
    _route_to_dlq(task_id, payload, db_path, error)
    
    # Verify entry pushed to Redis
    queue_length = redis_client.llen(DLQ_QUEUE_NAME)
    assert queue_length == 1
    
    # Verify entry content
    raw_entry = redis_client.lindex(DLQ_QUEUE_NAME, 0)
    entry = json.loads(raw_entry)
    
    assert entry["task_id"] == task_id
    assert entry["payload"] == payload
    assert entry["db_path"] == db_path
    assert entry["error"] == error
    assert entry["retry_count"] == 2
    assert "failed_at" in entry


def test_dlq_stats(redis_client):
    """
    Test: get_dlq_stats() returns queue statistics.
    """
    # Empty queue
    stats = get_dlq_stats()
    assert stats["queue_name"] == DLQ_QUEUE_NAME
    assert stats["total_items"] == 0
    assert stats["sample_entries"] == []
    
    # Add entries
    _route_to_dlq("task-1", {"data": "a"}, "db1.db", "Error 1")
    _route_to_dlq("task-2", {"data": "b"}, "db2.db", "Error 2")
    
    stats = get_dlq_stats()
    assert stats["total_items"] == 2
    assert len(stats["sample_entries"]) == 2


def test_inspect_dlq_task(redis_client):
    """
    Test: inspect_dlq_task() retrieves specific task from DLQ.
    """
    task_id = "inspect-test-task"
    payload = {"file_refs": ["file://path"]}
    error = "Test error"
    
    # Route to DLQ
    _route_to_dlq(task_id, payload, "db.db", error)
    
    # Inspect task
    entry = inspect_dlq_task(task_id)
    
    assert entry is not None
    assert entry["task_id"] == task_id
    assert entry["payload"] == payload
    assert entry["error"] == error


def test_inspect_dlq_task_not_found(redis_client):
    """
    Test: inspect_dlq_task() returns None if task not in DLQ.
    """
    entry = inspect_dlq_task("nonexistent-task")
    assert entry is None


def test_replay_dlq_task(redis_client):
    """
    Test: replay_dlq_task() re-enqueues task and removes from DLQ.
    """
    task_id = "replay-test-task"
    payload = {"file_refs": ["s3://bucket/file"]}
    db_path = "test.db"
    
    # Route to DLQ
    _route_to_dlq(task_id, payload, db_path, "Original error")
    
    # Verify in DLQ
    assert redis_client.llen(DLQ_QUEUE_NAME) == 1
    
    # Mock Celery apply_async
    with patch('src.worker.dlq.grade_homework_task') as mock_task:
        mock_result = Mock()
        mock_result.id = f"{task_id}-replayed"
        mock_task.apply_async.return_value = mock_result
        
        # Replay task
        new_task_id = replay_dlq_task(task_id, remove_from_dlq=True)
        
        # Verify task re-enqueued
        mock_task.apply_async.assert_called_once_with(
            args=[task_id, payload, db_path],
            task_id=task_id,
        )
        
        # Verify removed from DLQ
        assert redis_client.llen(DLQ_QUEUE_NAME) == 0


def test_replay_dlq_task_keep_in_dlq(redis_client):
    """
    Test: replay_dlq_task() can keep task in DLQ after replay.
    """
    task_id = "replay-keep-task"
    payload = {"file_refs": []}
    
    _route_to_dlq(task_id, payload, "db.db", "Error")
    
    with patch('src.worker.dlq.grade_homework_task') as mock_task:
        mock_task.apply_async.return_value = Mock(id="new-id")
        
        # Replay without removing
        replay_dlq_task(task_id, remove_from_dlq=False)
        
        # Verify still in DLQ
        assert redis_client.llen(DLQ_QUEUE_NAME) == 1


def test_replay_dlq_task_not_found(redis_client):
    """
    Test: replay_dlq_task() raises ValueError if task not in DLQ.
    """
    with pytest.raises(ValueError, match="not found in DLQ"):
        replay_dlq_task("nonexistent-task")


def test_purge_dlq_requires_confirmation(redis_client):
    """
    Test: purge_dlq() requires explicit confirmation.
    """
    # Add tasks
    _route_to_dlq("task-1", {}, "db.db", "Error 1")
    _route_to_dlq("task-2", {}, "db.db", "Error 2")
    
    # Try purge without confirmation
    with pytest.raises(RuntimeError, match="requires explicit confirmation"):
        purge_dlq(confirm=False)
    
    # Verify DLQ not purged
    assert redis_client.llen(DLQ_QUEUE_NAME) == 2


def test_purge_dlq_with_confirmation(redis_client):
    """
    Test: purge_dlq(confirm=True) deletes all tasks.
    """
    # Add tasks
    _route_to_dlq("task-1", {}, "db.db", "Error 1")
    _route_to_dlq("task-2", {}, "db.db", "Error 2")
    _route_to_dlq("task-3", {}, "db.db", "Error 3")
    
    # Purge with confirmation
    purged_count = purge_dlq(confirm=True)
    
    assert purged_count == 3
    assert redis_client.llen(DLQ_QUEUE_NAME) == 0


def test_list_dlq_tasks(redis_client):
    """
    Test: list_dlq_tasks() retrieves all tasks.
    """
    # Add multiple tasks
    for i in range(5):
        _route_to_dlq(f"task-{i}", {"index": i}, "db.db", f"Error {i}")
    
    # List all tasks
    tasks = list_dlq_tasks(limit=100)
    
    assert len(tasks) == 5
    assert all("task_id" in task for task in tasks)
    assert all("error" in task for task in tasks)


def test_list_dlq_tasks_with_limit(redis_client):
    """
    Test: list_dlq_tasks() respects limit parameter.
    """
    # Add 10 tasks
    for i in range(10):
        _route_to_dlq(f"task-{i}", {}, "db.db", f"Error {i}")
    
    # List with limit
    tasks = list_dlq_tasks(limit=3)
    
    assert len(tasks) == 3


def test_dlq_isolation_from_celery_broker(redis_client):
    """
    Test: DLQ is separate from Celery broker queue.
    
    Critical: Validates that DLQ uses explicit Redis list operations,
    not Celery's task routing (which doesn't support DLQ with Redis).
    """
    task_id = "isolation-test"
    
    # Route to DLQ
    _route_to_dlq(task_id, {"data": "test"}, "db.db", "Error")
    
    # Verify in DLQ (Redis list)
    dlq_entry = redis_client.lindex(DLQ_QUEUE_NAME, 0)
    assert dlq_entry is not None
    
    # Verify NOT in Celery broker queue
    # (Celery uses different key format: celery, celery:task_queue, etc.)
    celery_keys = redis_client.keys("celery*")
    
    # DLQ should be isolated
    assert DLQ_QUEUE_NAME not in [key.decode() if isinstance(key, bytes) else key for key in celery_keys]


def test_dlq_preserves_full_context(redis_client):
    """
    Test: DLQ entry contains complete task context for replay.
    """
    task_id = "context-test"
    payload = {
        "file_refs": ["s3://bucket/task/file1.png", "s3://bucket/task/file2.png"]
    }
    db_path = "/app/data/grading.db"
    error = "ValueError: Invalid input format at line 42\nTraceback: ..."
    
    # Route to DLQ
    _route_to_dlq(task_id, payload, db_path, error)
    
    # Retrieve and validate
    entry = inspect_dlq_task(task_id)
    
    # Must have all context for replay
    assert entry["task_id"] == task_id
    assert entry["payload"] == payload
    assert entry["db_path"] == db_path
    assert entry["error"] == error
    assert "failed_at" in entry
    assert entry["retry_count"] == 2
    
    # Verify timestamp is ISO format
    from datetime import datetime
    datetime.fromisoformat(entry["failed_at"])  # Should not raise


def test_multiple_failures_accumulate_in_dlq(redis_client):
    """
    Test: Multiple failed tasks accumulate in DLQ (FIFO order).
    """
    # Route 3 tasks to DLQ
    _route_to_dlq("task-A", {}, "db.db", "Error A")
    _route_to_dlq("task-B", {}, "db.db", "Error B")
    _route_to_dlq("task-C", {}, "db.db", "Error C")
    
    # Verify all in DLQ
    assert redis_client.llen(DLQ_QUEUE_NAME) == 3
    
    # Verify FIFO order (newest first, due to lpush)
    entries = list_dlq_tasks(limit=10)
    assert entries[0]["task_id"] == "task-C"  # Most recent
    assert entries[1]["task_id"] == "task-B"
    assert entries[2]["task_id"] == "task-A"  # Oldest
