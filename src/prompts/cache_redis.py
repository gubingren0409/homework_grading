import json
from typing import Optional

import redis.asyncio as aioredis

from src.prompts.schemas import PromptResolveResult


class RedisPromptCache:
    def __init__(self, redis_url: str, *, key_prefix: str = "prompt:l2:") -> None:
        self._client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._prefix = key_prefix

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> Optional[PromptResolveResult]:
        raw = await self._client.get(self._k(key))
        if not raw:
            return None
        obj = json.loads(raw)
        return PromptResolveResult(
            messages=obj["messages"],
            asset_version=obj["asset_version"],
            variant_id=obj["variant_id"],
            token_estimate=int(obj["token_estimate"]),
            cache_level=obj.get("cache_level", "L2"),
        )

    async def set(self, key: str, value: PromptResolveResult, ttl_seconds: int) -> None:
        payload = json.dumps(
            {
                "messages": value.messages,
                "asset_version": value.asset_version,
                "variant_id": value.variant_id,
                "token_estimate": value.token_estimate,
                "cache_level": "L2",
            },
            ensure_ascii=False,
        )
        await self._client.set(self._k(key), payload, ex=ttl_seconds)

    async def invalidate_prefix(self, prefix: str) -> int:
        cursor = 0
        deleted = 0
        pattern = self._k(f"{prefix}*")
        while True:
            cursor, keys = await self._client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                deleted += await self._client.delete(*keys)
            if cursor == 0:
                break
        return deleted

    async def close(self) -> None:
        await self._client.close()
