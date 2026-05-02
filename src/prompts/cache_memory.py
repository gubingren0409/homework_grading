import asyncio
import time
from dataclasses import dataclass
from typing import Dict

from src.prompts.schemas import PromptResolveResult


@dataclass
class _CacheEntry:
    value: PromptResolveResult
    expire_at: float
    soft_expire_at: float


class InMemoryPromptCache:
    """
    L1 cache with TTL + SWR semantics.
    """

    def __init__(self, ttl_seconds: int, swr_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._swr = swr_seconds
        self._data: Dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> PromptResolveResult | None:
        now = time.time()
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if now > entry.expire_at:
                self._data.pop(key, None)
                return None
            return entry.value

    async def get_state(self, key: str) -> tuple[PromptResolveResult | None, str]:
        now = time.time()
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None, "miss"
            if now > entry.expire_at:
                self._data.pop(key, None)
                return None, "miss"
            if now > entry.soft_expire_at:
                return entry.value, "stale"
            return entry.value, "fresh"

    async def set(self, key: str, value: PromptResolveResult, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        now = time.time()
        async with self._lock:
            self._data[key] = _CacheEntry(
                value=value,
                soft_expire_at=now + min(ttl, self._swr),
                expire_at=now + ttl,
            )

    async def invalidate_prefix(self, prefix: str) -> int:
        async with self._lock:
            keys = [k for k in self._data.keys() if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
            return len(keys)


class NoopPromptCache:
    async def get(self, key: str) -> PromptResolveResult | None:
        del key
        return None

    async def set(self, key: str, value: PromptResolveResult, ttl_seconds: int) -> None:
        del key, value, ttl_seconds

    async def invalidate_prefix(self, prefix: str) -> int:
        del prefix
        return 0
