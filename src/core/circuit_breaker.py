"""
Phase 34: Distributed Circuit Breaker - Redis-backed Global State

This module replaces the Phase 33 in-memory circuit breaker to fix state
isolation across worker processes. Circuit state is stored in Redis and shared
globally by all API/Worker instances.
"""

import asyncio
import time
import logging
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from src.core.config import settings


logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when the distributed circuit breaker is OPEN/HALF_OPEN."""


class CircuitBreaker:
    """
    Redis-backed distributed circuit breaker.

    State is stored under:
      - circuit:{name}:state
      - circuit:{name}:failures
      - circuit:{name}:successes
      - circuit:{name}:last_failure
      - circuit:{name}:probe_lock
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
        expected_exceptions: tuple = (Exception,),
        redis_client: Optional[aioredis.Redis] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.expected_exceptions = expected_exceptions

        self._state_key = f"circuit:{name}:state"
        self._failures_key = f"circuit:{name}:failures"
        self._successes_key = f"circuit:{name}:successes"
        self._last_failure_key = f"circuit:{name}:last_failure"
        self._probe_lock_key = f"circuit:{name}:probe_lock"

        self._redis_client = redis_client
        self._own_client = redis_client is None
        self._local_fallback_mode = not settings.circuit_breaker_redis_enabled
        self._local_probe_in_progress = False
        self._local_last_failure_ts: float | None = None
        # Compatibility mirrors for existing callers/tests; source of truth is Redis.
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self._last_state_change = time.time()

        logger.info(
            "[CircuitBreaker] Initialized distributed breaker %s "
            "(threshold=%s, recovery_timeout=%ss)",
            name,
            failure_threshold,
            recovery_timeout,
        )

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis_client is None:
            self._redis_client = await aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis_client

    # ---- Local fallback mode when Redis is unavailable ----
    def _activate_local_fallback(self, exc: Exception) -> None:
        if not self._local_fallback_mode:
            logger.warning(
                "[CircuitBreaker] %s redis backend unavailable, degrading to local mode: %s",
                self.name,
                exc,
            )
        self._local_fallback_mode = True

    def _local_remaining_timeout(self) -> float:
        if self._local_last_failure_ts is None:
            return 0.0
        elapsed = time.time() - self._local_last_failure_ts
        return max(0.0, self.recovery_timeout - elapsed)

    def _local_should_half_open(self) -> bool:
        return self._local_remaining_timeout() <= 0.0

    def _local_to_half_open(self) -> None:
        self.state = CircuitState.HALF_OPEN
        self.success_count = 0
        self._last_state_change = time.time()

    def _local_acquire_probe_lock(self) -> bool:
        if self._local_probe_in_progress:
            return False
        self._local_probe_in_progress = True
        return True

    def _local_on_success(self) -> None:
        if self.state == CircuitState.CLOSED:
            self.failure_count = 0
        elif self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                self._local_probe_in_progress = False
            else:
                self._local_probe_in_progress = False
        self._last_state_change = time.time()

    def _local_on_failure(self) -> None:
        now_ts = time.time()
        if self.state == CircuitState.CLOSED:
            self.failure_count += 1
            self._local_last_failure_ts = now_ts
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.failure_count = self.failure_threshold
                self.success_count = 0
        elif self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self._local_last_failure_ts = now_ts
            self.success_count = 0
            self._local_probe_in_progress = False
        self._last_state_change = time.time()

    async def _run_with_local_fallback(self, func: Callable, *args, **kwargs) -> Any:
        state = self.state
        if state == CircuitState.OPEN:
            if self._local_should_half_open():
                self._local_to_half_open()
                state = self.state
            else:
                remaining = self._local_remaining_timeout()
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name} is OPEN (local fallback). "
                    f"Retry in {remaining:.1f}s."
                )

        if state == CircuitState.HALF_OPEN:
            if not self._local_acquire_probe_lock():
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name} is HALF_OPEN (local fallback). Probe in progress."
                )

        try:
            result = await func(*args, **kwargs)
            self._local_on_success()
            return result
        except self.expected_exceptions:
            self._local_on_failure()
            raise

    async def _get_state(self, redis: aioredis.Redis) -> CircuitState:
        state = await redis.get(self._state_key)
        if state is None:
            return CircuitState.CLOSED
        return CircuitState(state)

    async def _sync_local_snapshot(self, redis: aioredis.Redis) -> None:
        state = await self._get_state(redis)
        self.state = state
        self.failure_count = int(await redis.get(self._failures_key) or 0)
        self.success_count = int(await redis.get(self._successes_key) or 0)
        self._last_state_change = time.time()

    async def _remaining_timeout(self, redis: aioredis.Redis) -> float:
        last_failure = await redis.get(self._last_failure_key)
        if last_failure is None:
            return 0.0
        elapsed = time.time() - float(last_failure)
        return max(0.0, self.recovery_timeout - elapsed)

    async def _should_half_open(self, redis: aioredis.Redis) -> bool:
        return await self._remaining_timeout(redis) <= 0.0

    async def _to_half_open(self, redis: aioredis.Redis) -> None:
        await redis.set(self._state_key, CircuitState.HALF_OPEN.value)
        await redis.delete(self._successes_key)
        await self._sync_local_snapshot(redis)

    async def _acquire_probe_lock(self, redis: aioredis.Redis) -> bool:
        # one probe request globally, auto-expire to avoid dead lock
        return bool(await redis.set(self._probe_lock_key, "1", nx=True, ex=15))

    async def _on_success(self, redis: aioredis.Redis) -> None:
        state = await self._get_state(redis)
        if state == CircuitState.CLOSED:
            # reset failures on any success in closed state
            await redis.delete(self._failures_key)
            result = "reset"
        elif state == CircuitState.HALF_OPEN:
            successes = await redis.incr(self._successes_key)
            if successes >= self.success_threshold:
                await redis.set(self._state_key, CircuitState.CLOSED.value)
                await redis.delete(
                    self._failures_key, self._successes_key, self._probe_lock_key
                )
                result = "closed"
            else:
                # Keep HALF_OPEN but release probe lock so next sequential probe can run.
                await redis.delete(self._probe_lock_key)
                result = "probing"
        else:
            result = "ignored"
        if result == "closed":
            logger.info("[CircuitBreaker] %s transitioned to CLOSED globally", self.name)
        await self._sync_local_snapshot(redis)

    async def _on_failure(self, redis: aioredis.Redis) -> None:
        now_ts = time.time()
        state = await self._get_state(redis)
        if state == CircuitState.CLOSED:
            failures = await redis.incr(self._failures_key)
            await redis.set(self._last_failure_key, now_ts)
            if failures >= self.failure_threshold:
                await redis.set(self._state_key, CircuitState.OPEN.value)
                await redis.delete(self._failures_key)
                result = "tripped"
            else:
                result = "recorded"
        elif state == CircuitState.HALF_OPEN:
            await redis.set(self._state_key, CircuitState.OPEN.value)
            await redis.set(self._last_failure_key, now_ts)
            await redis.delete(
                self._failures_key, self._successes_key, self._probe_lock_key
            )
            result = "reopened"
        else:
            result = "ignored"
        if result == "tripped":
            logger.error(
                "[CircuitBreaker] %s OPEN globally after %s failures",
                self.name,
                self.failure_threshold,
            )
            # Keep local mirror compatible with existing expectations.
            self.state = CircuitState.OPEN
            self.failure_count = self.failure_threshold
            self.success_count = 0
            self._last_state_change = time.time()
        else:
            await self._sync_local_snapshot(redis)

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            if self._local_fallback_mode:
                return await self._run_with_local_fallback(func, *args, **kwargs)
            try:
                redis = await self._get_redis()
                state = await self._get_state(redis)
                self.state = state

                if state == CircuitState.OPEN:
                    if await self._should_half_open(redis):
                        await self._to_half_open(redis)
                        state = CircuitState.HALF_OPEN
                        self.state = state
                    else:
                        remaining = await self._remaining_timeout(redis)
                        raise CircuitBreakerOpenError(
                            f"Circuit breaker {self.name} is OPEN globally. "
                            f"Retry in {remaining:.1f}s."
                        )

                if state == CircuitState.HALF_OPEN:
                    if not await self._acquire_probe_lock(redis):
                        raise CircuitBreakerOpenError(
                            f"Circuit breaker {self.name} is HALF_OPEN. Probe in progress."
                        )

                try:
                    result = await func(*args, **kwargs)
                    await self._on_success(redis)
                    return result
                except self.expected_exceptions:
                    await self._on_failure(redis)
                    raise
            except RedisError as exc:
                self._activate_local_fallback(exc)
                return await self._run_with_local_fallback(func, *args, **kwargs)

        return wrapper

    async def _get_state_dict(self) -> dict:
        if self._local_fallback_mode:
            state_uptime = time.time() - self._last_state_change
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": int(self.failure_count),
                "success_count": int(self.success_count),
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "last_failure_time": float(self._local_last_failure_ts) if self._local_last_failure_ts else None,
                "state_uptime_seconds": state_uptime,
                "backend": "local_fallback",
            }
        try:
            redis = await self._get_redis()
            state = await self._get_state(redis)
            failures = int(await redis.get(self._failures_key) or 0)
            successes = int(await redis.get(self._successes_key) or 0)
            last_failure = await redis.get(self._last_failure_key)
            state_uptime = time.time() - self._last_state_change
            return {
                "name": self.name,
                "state": state.value,
                "failure_count": failures,
                "success_count": successes,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "last_failure_time": float(last_failure) if last_failure else None,
                "state_uptime_seconds": state_uptime,
                "backend": "redis_distributed",
            }
        except RedisError as exc:
            self._activate_local_fallback(exc)
            state_uptime = time.time() - self._last_state_change
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": int(self.failure_count),
                "success_count": int(self.success_count),
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "last_failure_time": float(self._local_last_failure_ts) if self._local_last_failure_ts else None,
                "state_uptime_seconds": state_uptime,
                "backend": "local_fallback",
            }

    async def reset(self) -> None:
        if self._local_fallback_mode:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self._local_last_failure_ts = None
            self._local_probe_in_progress = False
            self._last_state_change = time.time()
            return
        try:
            redis = await self._get_redis()
            await redis.set(self._state_key, CircuitState.CLOSED.value)
            await redis.delete(
                self._failures_key,
                self._successes_key,
                self._last_failure_key,
                self._probe_lock_key,
            )
            await self._sync_local_snapshot(redis)
        except RedisError as exc:
            self._activate_local_fallback(exc)
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self._local_last_failure_ts = None
            self._local_probe_in_progress = False
            self._last_state_change = time.time()

    def _run_sync(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError("Cannot call sync helper while event loop is running; use await.")

    def snapshot(self):
        """Sync helper for tests/tools; async code should use await get_state()."""
        return self._run_sync(self._get_state_dict())

    async def close(self) -> None:
        if self._own_client and self._redis_client is not None:
            await self._redis_client.close()
            self._redis_client = None

