import json
from typing import Optional

import redis.asyncio as aioredis

from src.prompts.schemas import PromptInvalidationEvent


class RedisInvalidationBus:
    def __init__(self, redis_url: str, channel: str = "prompt:invalidate") -> None:
        self._client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._pubsub = self._client.pubsub()
        self._channel = channel

    async def publish(self, event: PromptInvalidationEvent) -> None:
        payload = json.dumps(
            {
                "prompt_key": event.prompt_key,
                "version_hash": event.version_hash,
                "source": event.source,
            },
            ensure_ascii=False,
        )
        await self._client.publish(self._channel, payload)

    async def subscribe(self) -> None:
        await self._pubsub.subscribe(self._channel)

    async def recv(self) -> Optional[PromptInvalidationEvent]:
        msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if not msg:
            return None
        data = msg.get("data")
        if not isinstance(data, str):
            return None
        payload = json.loads(data)
        return PromptInvalidationEvent(
            prompt_key=payload["prompt_key"],
            version_hash=payload["version_hash"],
            source=payload.get("source", "unknown"),
        )

    async def close(self) -> None:
        await self._pubsub.close()
        await self._client.close()
