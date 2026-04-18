import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, Optional

from sse_starlette.sse import EventSourceResponse
import redis
import redis.asyncio as aioredis

from src.core.config import settings
from src.db.client import get_task


logger = logging.getLogger(__name__)

_REDIS_POOL = aioredis.ConnectionPool.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    max_connections=200,
)


def _get_redis_client() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=_REDIS_POOL)


def get_task_channel(task_id: str) -> str:
    return f"task_status:{task_id}"


def _status_event_fingerprint(event_data: Dict[str, Any]) -> str:
    comparable = {
        "status": event_data.get("status"),
        "grading_status": event_data.get("grading_status"),
        "progress": event_data.get("progress"),
        "eta_seconds": event_data.get("eta_seconds"),
        "error": event_data.get("error"),
        "message": event_data.get("message"),
    }
    return json.dumps(comparable, sort_keys=True, ensure_ascii=False, default=str)


async def _build_status_event(task: Dict[str, Any], source: str) -> Dict[str, Any]:
    event_data = {
        "task_id": task["task_id"],
        "status": task["status"],
        "source": source,
    }
    if task["status"] in ["PENDING", "PROCESSING"]:
        event_data["progress"] = float(task.get("progress") or 0.0)
        eta_value = task.get("eta_seconds")
        event_data["eta_seconds"] = int(eta_value) if eta_value is not None else 60
    elif task["status"] == "COMPLETED":
        event_data["message"] = "Grading completed successfully"
    elif task["status"] == "FAILED":
        raw_error = task.get("error_message", "Unknown error")
        event_data["error"] = raw_error[:200]
    return event_data


async def task_status_stream(
    db_path: str,
    task_id: str,
    timeout: int = 300,
    fallback_poll_interval: float = 5.0,  # kept for compatibility, no DB polling fallback anymore
    heartbeat_interval: float = 15.0,
) -> AsyncGenerator[dict, None]:
    _ = fallback_poll_interval
    start_time = time.monotonic()
    last_ping = start_time
    last_status: Optional[str] = None
    last_event_fingerprint: Optional[str] = None

    task = await get_task(db_path, task_id)
    if not task:
        yield {"event": "error", "data": json.dumps({"error": "Task not found"})}
        return

    initial_event = await _build_status_event(task, source="initial")
    yield {"event": "status_update", "data": json.dumps(initial_event)}
    last_status = task["status"]
    last_event_fingerprint = _status_event_fingerprint(initial_event)
    if last_status in ["COMPLETED", "FAILED"]:
        yield {"event": "complete", "data": json.dumps({"task_id": task_id, "status": last_status})}
        return

    channel = get_task_channel(task_id)
    redis_client = _get_redis_client()
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(channel)
    except (redis.exceptions.RedisError, asyncio.TimeoutError):
        yield {"event": "error", "data": json.dumps({"code": "SSE_BACKEND_UNAVAILABLE"})}
        return

    try:
        while True:
            now = time.monotonic()
            if now - start_time > timeout:
                yield {"event": "timeout", "data": json.dumps({"message": "Stream timeout exceeded"})}
                break

            if now - last_ping >= heartbeat_interval:
                yield {"event": "ping", "data": "{}"}
                last_ping = now

            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except redis.exceptions.RedisError:
                yield {"event": "error", "data": json.dumps({"code": "SSE_BACKEND_UNAVAILABLE"})}
                break

            if not message or message.get("type") != "message":
                continue

            raw_data = message.get("data")
            try:
                event_data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            except json.JSONDecodeError:
                logger.warning("[SSE] dropped malformed pubsub payload for task %s", task_id)
                continue

            if not isinstance(event_data, dict):
                continue
            event_data["source"] = "pubsub"
            current_status = event_data.get("status")
            if not current_status:
                continue
            current_fingerprint = _status_event_fingerprint(event_data)
            if current_fingerprint != last_event_fingerprint:
                yield {"event": "status_update", "data": json.dumps(event_data)}
                last_event_fingerprint = current_fingerprint
                last_status = str(current_status)
                if last_status in ["COMPLETED", "FAILED"]:
                    yield {"event": "complete", "data": json.dumps({"task_id": task_id, "status": last_status})}
                    break
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        try:
            await pubsub.aclose()
        except Exception:
            pass


def create_sse_response(db_path: str, task_id: str) -> EventSourceResponse:
    return EventSourceResponse(
        task_status_stream(
            db_path,
            task_id,
            timeout=settings.sse_stream_timeout_seconds,
            heartbeat_interval=settings.sse_heartbeat_interval_seconds,
        ),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def publish_task_status(task_id: str, status: str, **kwargs) -> None:
    redis_client = _get_redis_client()
    channel = get_task_channel(task_id)
    event_data = {
        "task_id": task_id,
        "status": status,
        **kwargs,
    }
    try:
        await redis_client.publish(channel, json.dumps(event_data))
    except redis.exceptions.RedisError as exc:
        logger.error(f"[PubSub] Failed to publish task {task_id} status: {exc}")
