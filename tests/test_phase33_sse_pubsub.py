"""
Phase 33: SSE Redis Pub/Sub Integration Tests

Tests for distributed event-driven SSE:
1. Worker publishes status updates to Redis
2. SSE client receives updates via Pub/Sub
3. Fallback to database polling if Pub/Sub fails
4. Multi-node simulation (Worker on Node A, SSE on Node B)
"""
import asyncio
import pytest
import pytest_asyncio
import json
from unittest.mock import Mock, patch, AsyncMock

import redis.asyncio as aioredis

from src.api.sse import (
    task_status_stream,
    get_task_channel,
    publish_task_status,
)
from src.core.config import settings


@pytest_asyncio.fixture
async def redis_client():
    """Redis client for testing."""
    client = await aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def mock_db_task():
    """Mock database task record."""
    return {
        "task_id": "test-task-123",
        "status": "PENDING",
        "error_message": None,
    }


@pytest.mark.asyncio
async def test_get_task_channel():
    """Test: Redis channel naming convention."""
    task_id = "abc-123"
    channel = get_task_channel(task_id)
    assert channel == "task_status:abc-123"


@pytest.mark.asyncio
async def test_publish_task_status(redis_client):
    """
    Test: Worker can publish status updates to Redis Pub/Sub.
    """
    task_id = "pub-test-task"
    
    # Subscribe to channel
    pubsub = redis_client.pubsub()
    channel = get_task_channel(task_id)
    await pubsub.subscribe(channel)
    
    # Skip subscribe confirmation message
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    
    # Publish status update
    await publish_task_status(task_id, "PROCESSING", progress=0.5)
    
    # Receive published message
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
    
    assert message is not None
    assert message["type"] == "message"
    
    data = json.loads(message["data"])
    assert data["task_id"] == task_id
    assert data["status"] == "PROCESSING"
    assert data["progress"] == 0.5
    
    await pubsub.unsubscribe(channel)
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_sse_stream_receives_pubsub_update(redis_client, mock_db_task):
    """
    Test: SSE stream receives Worker-published updates via Pub/Sub.
    
    Simulates multi-node scenario:
    - Worker (Node A) publishes to Redis
    - SSE client (Node B) receives via Pub/Sub
    """
    task_id = "sse-pubsub-test"
    
    # Mock get_task to return task record
    with patch('src.api.sse.get_task', new_callable=AsyncMock) as mock_get_task:
        mock_get_task.return_value = {
            "task_id": task_id,
            "status": "PENDING",
            "error_message": None,
        }
        
        # Start SSE stream
        stream_gen = task_status_stream(
            db_path="mock.db",
            task_id=task_id,
            timeout=10,
            fallback_poll_interval=10,  # Disable fallback
        )
        
        # Consume initial event (from DB)
        initial_event = await stream_gen.__anext__()
        assert initial_event["event"] == "status_update"
        data = json.loads(initial_event["data"])
        assert data["status"] == "PENDING"
        assert data["source"] == "initial"
        
        # Simulate Worker publishing status update (multi-node scenario)
        await asyncio.sleep(0.5)  # Give SSE time to subscribe
        await publish_task_status(task_id, "PROCESSING", progress=0.5)
        
        # SSE should receive Pub/Sub update
        update_event = await asyncio.wait_for(stream_gen.__anext__(), timeout=3.0)
        assert update_event["event"] == "status_update"
        data = json.loads(update_event["data"])
        assert data["status"] == "PROCESSING"
        assert data["progress"] == 0.5
        assert data["source"] == "pubsub"
        
        # Publish terminal state
        await publish_task_status(task_id, "COMPLETED", message="Done")
        
        # SSE should receive completion update and close
        complete_update = await stream_gen.__anext__()
        assert complete_update["event"] == "status_update"
        data = json.loads(complete_update["data"])
        assert data["status"] == "COMPLETED"
        assert data["source"] == "pubsub"
        
        terminal_event = await stream_gen.__anext__()
        assert terminal_event["event"] == "complete"
        
        # Stream should terminate
        with pytest.raises(StopAsyncIteration):
            await stream_gen.__anext__()


@pytest.mark.asyncio
async def test_sse_stream_fallback_to_db_polling():
    """
    Test: SSE stream falls back to database polling if Pub/Sub fails.
    
    Scenario: Pub/Sub message lost, SSE detects via DB polling.
    """
    task_id = "fallback-test"
    
    # Mock get_task to simulate status changes
    call_count = 0
    async def mock_get_task_changing(db_path, task_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"task_id": task_id, "status": "PENDING", "error_message": None}
        else:
            # Status changed (simulating Worker updated DB but Pub/Sub failed)
            return {"task_id": task_id, "status": "COMPLETED", "error_message": None}
    
    with patch('src.api.sse.get_task', side_effect=mock_get_task_changing):
        stream_gen = task_status_stream(
            db_path="mock.db",
            task_id=task_id,
            timeout=15,
            fallback_poll_interval=2.0,  # Poll every 2s
        )
        
        # Initial event
        initial_event = await stream_gen.__anext__()
        assert json.loads(initial_event["data"])["status"] == "PENDING"
        
        # Wait for fallback polling (2s)
        await asyncio.sleep(2.5)
        
        # SSE should detect status change via DB fallback
        update_event = await asyncio.wait_for(stream_gen.__anext__(), timeout=5.0)
        assert update_event["event"] == "status_update"
        data = json.loads(update_event["data"])
        assert data["status"] == "COMPLETED"
        assert data["source"] == "fallback"  # Detected via DB polling
        
        # Terminal event
        terminal_event = await stream_gen.__anext__()
        assert terminal_event["event"] == "complete"


@pytest.mark.asyncio
async def test_sse_stream_timeout():
    """
    Test: SSE stream times out if task doesn't complete within timeout.
    """
    task_id = "timeout-test"
    
    with patch('src.api.sse.get_task', new_callable=AsyncMock) as mock_get_task:
        mock_get_task.return_value = {
            "task_id": task_id,
            "status": "PROCESSING",  # Never completes
            "error_message": None,
        }
        
        stream_gen = task_status_stream(
            db_path="mock.db",
            task_id=task_id,
            timeout=3,  # 3s timeout
            fallback_poll_interval=10,  # Disable fallback
        )
        
        # Initial event
        await stream_gen.__anext__()
        
        # Wait for timeout
        timeout_event = await asyncio.wait_for(stream_gen.__anext__(), timeout=5.0)
        assert timeout_event["event"] == "timeout"


@pytest.mark.asyncio
async def test_sse_stream_client_disconnect():
    """
    Test: SSE stream handles client disconnect gracefully.
    """
    task_id = "disconnect-test"
    
    with patch('src.api.sse.get_task', new_callable=AsyncMock) as mock_get_task:
        mock_get_task.return_value = {
            "task_id": task_id,
            "status": "PROCESSING",
            "error_message": None,
        }
        
        stream_gen = task_status_stream(
            db_path="mock.db",
            task_id=task_id,
            timeout=10,
        )
        
        # Consume initial event
        await stream_gen.__anext__()
        
        # Simulate client disconnect
        await stream_gen.aclose()


@pytest.mark.asyncio
async def test_multi_node_simulation(redis_client):
    """
    Test: Multi-node deployment simulation.
    
    Scenario:
    1. Worker on Node A updates DB + publishes to Redis
    2. API on Node B (different physical machine) receives via Pub/Sub
    3. API forwards to SSE client
    """
    task_id = "multi-node-test"
    
    # Node B: Start SSE stream (API server)
    with patch('src.api.sse.get_task', new_callable=AsyncMock) as mock_get_task:
        mock_get_task.return_value = {
            "task_id": task_id,
            "status": "PENDING",
            "error_message": None,
        }
        
        stream_gen = task_status_stream(
            db_path="node_b.db",
            task_id=task_id,
            timeout=10,
            fallback_poll_interval=10,
        )
        
        # Consume initial event
        await stream_gen.__anext__()
        
        # Node A: Worker publishes status update (different machine)
        await asyncio.sleep(0.5)
        await publish_task_status(task_id, "PROCESSING", progress=0.3)
        
        # Node B: API receives update via Redis Pub/Sub
        event = await asyncio.wait_for(stream_gen.__anext__(), timeout=3.0)
        data = json.loads(event["data"])
        assert data["status"] == "PROCESSING"
        assert data["source"] == "pubsub"
        
        # Node A: Worker completes task
        await publish_task_status(task_id, "COMPLETED")
        
        # Node B: API receives completion
        event = await stream_gen.__anext__()
        data = json.loads(event["data"])
        assert data["status"] == "COMPLETED"
        
        # Stream terminates
        terminal_event = await stream_gen.__anext__()
        assert terminal_event["event"] == "complete"
