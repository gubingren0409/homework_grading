import json
import logging
from datetime import UTC, datetime
from typing import Any, Dict

from src.core.config import settings
from src.core.trace_context import get_trace_id


logger = logging.getLogger(__name__)


async def publish_status(task_id: str, status: str, **kwargs) -> None:
    import redis.asyncio as aioredis

    redis_client = None
    try:
        redis_client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        channel = f"task_status:{task_id}"
        event_data = {
            "task_id": task_id,
            "status": status,
            "trace_id": get_trace_id(),
            **kwargs,
        }
        await redis_client.publish(channel, json.dumps(event_data))
        logger.info(f"[Worker-PubSub] Published status update for task {task_id}: {status}")
    except Exception as exc:
        logger.warning(f"[Worker-PubSub] Failed to publish task {task_id} status: {exc}")
    finally:
        if redis_client:
            await redis_client.aclose()


def route_to_dlq(*, dlq_queue_name: str, task_id: str, payload: Dict[str, Any], db_path: str, error: str) -> None:
    import redis

    try:
        redis_client = redis.from_url(settings.redis_url)
        dlq_entry = {
            "task_id": task_id,
            "trace_id": get_trace_id(),
            "payload": payload,
            "db_path": db_path,
            "error": error,
            "failed_at": datetime.now(UTC).isoformat(),
            "retry_count": 2,
        }
        redis_client.lpush(dlq_queue_name, json.dumps(dlq_entry))
        logger.warning(
            f"[DLQ] Task {task_id} routed to dead letter queue. "
            f"Error: {error[:100]}"
        )
    except Exception as dlq_error:
        logger.error(f"[DLQ] Failed to route task {task_id} to DLQ: {dlq_error}")

