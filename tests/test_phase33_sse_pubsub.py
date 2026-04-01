import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import redis

from src.api.sse import get_task_channel, task_status_stream


@pytest.mark.asyncio
async def test_get_task_channel():
    assert get_task_channel("abc-123") == "task_status:abc-123"


@pytest.mark.asyncio
async def test_sse_timeouterror_does_not_close_stream():
    task_id = "timeout-no-disconnect"
    initial_task = {"task_id": task_id, "status": "PENDING", "error_message": None}

    class FakePubSub:
        def __init__(self):
            self.calls = 0

        async def subscribe(self, _channel):
            return None

        async def get_message(self, ignore_subscribe_messages=True):
            self.calls += 1
            if self.calls <= 2:
                await asyncio.sleep(2.0)  # wait_for(1.0) => asyncio.TimeoutError
                return None
            return {"type": "message", "data": json.dumps({"task_id": task_id, "status": "COMPLETED"})}

        async def unsubscribe(self, _channel):
            return None

        async def aclose(self):
            return None

    class FakeRedis:
        def pubsub(self):
            return FakePubSub()

    with patch("src.api.sse.get_task", new=AsyncMock(return_value=initial_task)), patch(
        "src.api.sse._get_redis_client", return_value=FakeRedis()
    ):
        stream_gen = task_status_stream(db_path="mock.db", task_id=task_id, timeout=10, heartbeat_interval=999)
        first = await stream_gen.__anext__()
        assert first["event"] == "status_update"
        second = await asyncio.wait_for(stream_gen.__anext__(), timeout=6.0)
        assert second["event"] == "status_update"
        data = json.loads(second["data"])
        assert data["status"] == "COMPLETED"
        terminal = await stream_gen.__anext__()
        assert terminal["event"] == "complete"


@pytest.mark.asyncio
async def test_sse_redis_error_emits_error_and_closes():
    task_id = "redis-error-close"
    initial_task = {"task_id": task_id, "status": "PENDING", "error_message": None}

    class FakePubSub:
        async def subscribe(self, _channel):
            return None

        async def get_message(self, ignore_subscribe_messages=True):
            raise redis.exceptions.ConnectionError("redis down")

        async def unsubscribe(self, _channel):
            return None

        async def aclose(self):
            return None

    class FakeRedis:
        def pubsub(self):
            return FakePubSub()

    with patch("src.api.sse.get_task", new=AsyncMock(return_value=initial_task)), patch(
        "src.api.sse._get_redis_client", return_value=FakeRedis()
    ):
        stream_gen = task_status_stream(db_path="mock.db", task_id=task_id, timeout=10)
        first = await stream_gen.__anext__()
        assert first["event"] == "status_update"
        second = await stream_gen.__anext__()
        assert second["event"] == "error"
        payload = json.loads(second["data"])
        assert payload["code"] == "SSE_BACKEND_UNAVAILABLE"
        with pytest.raises(StopAsyncIteration):
            await stream_gen.__anext__()
