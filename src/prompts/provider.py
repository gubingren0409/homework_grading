import asyncio
import copy
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Optional

from src.prompts.cache_memory import InMemoryPromptCache, NoopPromptCache
from src.prompts.cache_redis import RedisPromptCache
from src.prompts.exceptions import PromptAssetNotFound
from src.prompts.guards import validate_budget_or_raise
from src.prompts.payloads import build_openai_user_content, validate_variable_kinds, variables_to_map
from src.prompts.routing import choose_variant, choose_variant_with_weights
from src.prompts.schemas import (
    PromptInvalidationEvent,
    PromptLKGSnapshot,
    PromptResolveRequest,
    PromptResolveResult,
    PromptVariable,
    RefreshReport,
)
from src.prompts.source_file import FilePromptSource
from src.prompts.templating import render_template
from src.prompts.tokens import estimate_tokens
from src.core.config import settings
from src.core.trace_context import bind_context, reset_context


logger = logging.getLogger(__name__)
_DEFAULT_PROMPT_PROVIDER: "PromptProviderService | None" = None


class PromptProviderService:
    def __init__(
        self,
        *,
        source: FilePromptSource,
        l1_cache: InMemoryPromptCache,
        l2_cache: RedisPromptCache | NoopPromptCache,
        invalidation_bus=None,
        pull_interval_seconds: int = 30,
        l2_ttl_seconds: int = 1800,
        l1_ttl_seconds: int = 120,
    ) -> None:
        self._source = source
        self._l1 = l1_cache
        self._l2 = l2_cache
        self._bus = invalidation_bus
        self._pull_interval_seconds = pull_interval_seconds
        self._l2_ttl_seconds = l2_ttl_seconds
        self._l1_ttl_seconds = l1_ttl_seconds
        self._singleflight: Dict[str, asyncio.Lock] = {}
        self._singleflight_guard = asyncio.Lock()
        self._lkg: Dict[str, PromptLKGSnapshot] = {}
        self._version_hash_snapshot: Dict[str, str] = {}
        self._pull_task: Optional[asyncio.Task] = None
        self._bus_task: Optional[asyncio.Task] = None
        self._forced_variant: Dict[str, str] = {}
        self._lkg_mode: Dict[str, bool] = {}
        self._ab_configs: Dict[str, Dict[str, Any]] = {}

    async def start(self) -> None:
        if self._pull_task and not self._pull_task.done():
            return
        self._version_hash_snapshot = dict(await self._source.list_assets_version_hash())
        if self._bus is not None:
            try:
                await self._bus.subscribe()
                self._bus_task = asyncio.create_task(self._run_bus_listener(), name="prompt-bus-listener")
            except Exception as exc:
                logger.warning(f"prompt invalidation bus unavailable, continue with pull-only mode: {exc}")
                self._bus = None
        self._pull_task = asyncio.create_task(self._run_pull_reconciler(), name="prompt-pull-reconciler")

    async def stop(self) -> None:
        tasks = [t for t in [self._bus_task, self._pull_task] if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._bus_task = None
        self._pull_task = None
        if self._bus is not None:
            close_fn = getattr(self._bus, "close", None)
            if callable(close_fn):
                try:
                    maybe_awaitable = close_fn()
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
                except Exception:
                    logger.warning("prompt invalidation bus close failed")
        close_l2 = getattr(self._l2, "close", None)
        if callable(close_l2):
            try:
                maybe_awaitable = close_l2()
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable
            except Exception:
                logger.warning("prompt l2 cache close failed")

    async def resolve(self, req: PromptResolveRequest) -> PromptResolveResult:
        tokens = bind_context(trace_id=req.trace_id, component="prompt-provider")
        try:
            if self._lkg_mode.get(req.prompt_key, False):
                lkg = self._lkg.get(req.prompt_key)
                if lkg is not None:
                    return self._with_cache_level(lkg.result, "LKG")
                raise PromptAssetNotFound(f"LKG mode enabled but no LKG snapshot for {req.prompt_key}")

            asset = await self._source.get_asset(req.prompt_key, req.locale)
            if asset.meta.prompt_key != req.prompt_key:
                raise PromptAssetNotFound(f"Prompt key mismatch: expected={req.prompt_key}, got={asset.meta.prompt_key}")
            variant_ids = [v.variant_id for v in asset.variants]
            forced_variant = self._forced_variant.get(req.prompt_key)
            if forced_variant and forced_variant in variant_ids:
                selected_variant = forced_variant
            else:
                selected_variant = self._choose_variant_with_ab(req=req, variants=variant_ids)
            variables_fingerprint = self._variables_fingerprint(req.variables)
            cache_key = (
                f"{req.prompt_key}:{asset.meta.version_hash}:{selected_variant}:{req.model}:"
                f"{variables_fingerprint}"
            )

            cached, state = await self._l1.get_state(cache_key)
            if cached is not None and state == "fresh":
                return self._with_cache_level(cached, "L1")

            if cached is not None and state == "stale":
                asyncio.create_task(self._refresh_cache_key(req, asset.meta.version_hash, selected_variant))
                return self._with_cache_level(cached, "L1")

            try:
                l2_value = await self._l2.get(cache_key)
            except Exception as exc:
                logger.warning(f"prompt l2 get failed, bypassing to source build: {exc}")
                l2_value = None
            if l2_value is not None:
                await self._l1.set(cache_key, self._with_cache_level(l2_value, "L1"), ttl_seconds=self._l1_ttl_seconds)
                return self._with_cache_level(l2_value, "L2")

            lock = await self._get_key_lock(cache_key)
            async with lock:
                # Double-check after waiting lock.
                cached2 = await self._l1.get(cache_key)
                if cached2 is not None:
                    return self._with_cache_level(cached2, "L1")

                resolved = await self._build_from_source(req, selected_variant)
                try:
                    await self._l2.set(cache_key, resolved, ttl_seconds=self._l2_ttl_seconds)
                except Exception as exc:
                    logger.warning(f"prompt l2 set failed, keeping l1/source path only: {exc}")
                await self._l1.set(cache_key, self._with_cache_level(resolved, "L1"), ttl_seconds=self._l1_ttl_seconds)
                self._lkg[req.prompt_key] = PromptLKGSnapshot(
                    prompt_key=req.prompt_key,
                    version_hash=asset.meta.version_hash,
                    result=self._with_cache_level(resolved, "LKG"),
                )
                return resolved
        except Exception:
            lkg = self._lkg.get(req.prompt_key)
            if lkg is not None:
                logger.warning(
                    "prompt_provider_lkg_fallback",
                    extra={
                        "extra_fields": {
                            "prompt_key": req.prompt_key,
                            "version_hash": lkg.version_hash,
                        }
                    },
                )
                return self._with_cache_level(lkg.result, "LKG")
            raise
        finally:
            reset_context(tokens)

    def set_forced_variant(self, *, prompt_key: str, variant_id: Optional[str]) -> None:
        if variant_id is None or not str(variant_id).strip():
            self._forced_variant.pop(prompt_key, None)
            return
        self._forced_variant[prompt_key] = str(variant_id)

    def get_forced_variant(self, *, prompt_key: str) -> Optional[str]:
        return self._forced_variant.get(prompt_key)

    def set_lkg_mode(self, *, prompt_key: str, enabled: bool) -> None:
        self._lkg_mode[prompt_key] = bool(enabled)

    def get_lkg_mode(self, *, prompt_key: str) -> bool:
        return bool(self._lkg_mode.get(prompt_key, False))

    def set_ab_config(
        self,
        *,
        prompt_key: str,
        enabled: bool,
        rollout_percentage: int,
        variant_weights: Mapping[str, int],
        segment_prefixes: Sequence[str],
        sticky_salt: str,
    ) -> None:
        rollout = int(rollout_percentage)
        if rollout < 0 or rollout > 100:
            raise ValueError("rollout_percentage must be in [0, 100]")
        self._ab_configs[prompt_key] = {
            "enabled": bool(enabled),
            "rollout_percentage": rollout,
            "variant_weights": {str(k): int(v) for k, v in variant_weights.items()},
            "segment_prefixes": [str(x) for x in segment_prefixes],
            "sticky_salt": str(sticky_salt or ""),
        }

    def get_ab_config(self, *, prompt_key: str) -> Dict[str, Any]:
        cfg = self._ab_configs.get(prompt_key)
        if cfg is None:
            return {
                "enabled": False,
                "rollout_percentage": 100,
                "variant_weights": {},
                "segment_prefixes": [],
                "sticky_salt": "",
            }
        return dict(cfg)

    async def invalidate(self, event: PromptInvalidationEvent) -> None:
        prefix = f"{event.prompt_key}:"
        l1_deleted = await self._l1.invalidate_prefix(prefix)
        l2_deleted = 0
        try:
            l2_deleted = await self._l2.invalidate_prefix(prefix)
        except Exception as exc:
            logger.warning(f"prompt l2 invalidate failed, keep l1 invalidation only: {exc}")
        logger.info(
            "prompt_cache_invalidated",
            extra={
                "extra_fields": {
                    "prompt_key": event.prompt_key,
                    "version_hash": event.version_hash,
                    "l1_deleted": l1_deleted,
                    "l2_deleted": l2_deleted,
                    "source": event.source,
                }
            },
        )

    async def refresh(self, prompt_key: str | None = None) -> RefreshReport:
        if prompt_key:
            await self.invalidate(
                PromptInvalidationEvent(
                    prompt_key=prompt_key,
                    version_hash=self._version_hash_snapshot.get(prompt_key, ""),
                    source="manual-refresh",
                )
            )
            return RefreshReport(checked_assets=1, refreshed_assets=1, invalidated_assets=1)

        hashes = await self._source.list_assets_version_hash()
        refreshed = 0
        invalidated = 0
        for key, new_hash in hashes.items():
            old_hash = self._version_hash_snapshot.get(key)
            if old_hash != new_hash:
                refreshed += 1
                await self.invalidate(
                    PromptInvalidationEvent(
                        prompt_key=key,
                        version_hash=new_hash,
                        source="pull-reconcile",
                    )
                )
                invalidated += 1
        self._version_hash_snapshot = dict(hashes)
        return RefreshReport(
            checked_assets=len(hashes),
            refreshed_assets=refreshed,
            invalidated_assets=invalidated,
        )

    async def _refresh_cache_key(
        self,
        req: PromptResolveRequest,
        version_hash: str,
        variant_id: str,
    ) -> None:
        variables_fingerprint = self._variables_fingerprint(req.variables)
        cache_key = f"{req.prompt_key}:{version_hash}:{variant_id}:{req.model}:{variables_fingerprint}"
        lock = await self._get_key_lock(cache_key)
        async with lock:
            value = await self._build_from_source(req, variant_id)
            try:
                await self._l2.set(cache_key, value, ttl_seconds=self._l2_ttl_seconds)
            except Exception as exc:
                logger.warning(f"prompt l2 set failed during background refresh: {exc}")
            await self._l1.set(cache_key, self._with_cache_level(value, "L1"), ttl_seconds=self._l1_ttl_seconds)

    async def _build_from_source(self, req: PromptResolveRequest, variant_id: str) -> PromptResolveResult:
        asset = await self._source.get_asset(req.prompt_key, req.locale)
        var_map = variables_to_map(req.variables)
        validate_variable_kinds(actual=var_map, expected_schema=asset.variables_schema)
        variant = next((v for v in asset.variants if v.variant_id == variant_id), None)
        if variant is None:
            raise PromptAssetNotFound(f"Variant not found: prompt_key={req.prompt_key}, variant={variant_id}")

        render_vars = {name: item.value for name, item in var_map.items() if item.kind == "text"}
        system_text = render_template(variant.system_template, render_vars)
        user_text = render_template(variant.user_template, render_vars)
        user_content = build_openai_user_content(user_text=user_text, variables=var_map)
        messages: Sequence[dict] = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_content},
        ]
        token_estimate = estimate_tokens(messages, req.model)
        validate_budget_or_raise(
            token_estimate=token_estimate,
            max_input_tokens=req.max_input_tokens,
            reserve_output_tokens=req.reserve_output_tokens,
        )
        logger.info(
            "prompt_exposure",
            extra={
                "extra_fields": {
                    "prompt_key": req.prompt_key,
                    "asset_version": asset.meta.version,
                    "version_hash": asset.meta.version_hash,
                    "variant_id": variant_id,
                    "model": req.model,
                    "trace_id": req.trace_id,
                }
            },
        )
        return PromptResolveResult(
            messages=list(messages),
            asset_version=asset.meta.version,
            variant_id=variant_id,
            token_estimate=token_estimate,
            cache_level="SOURCE",
        )

    async def _get_key_lock(self, cache_key: str) -> asyncio.Lock:
        async with self._singleflight_guard:
            lock = self._singleflight.get(cache_key)
            if lock is None:
                lock = asyncio.Lock()
                self._singleflight[cache_key] = lock
            return lock

    @staticmethod
    def _variables_fingerprint(variables: Sequence[PromptVariable]) -> str:
        """
        Build a stable digest for prompt variables so cache entries are bound to
        the exact input payload (including image_base64 values).
        """
        hasher = hashlib.sha256()
        for item in variables:
            hasher.update(str(item.name).encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(str(item.kind).encode("utf-8"))
            hasher.update(b"\x00")
            value_hash = hashlib.sha256(str(item.value).encode("utf-8")).hexdigest()
            hasher.update(value_hash.encode("ascii"))
            hasher.update(b"\x00")
        return hasher.hexdigest()

    def _choose_variant_with_ab(self, *, req: PromptResolveRequest, variants: Sequence[str]) -> str:
        cfg = self.get_ab_config(prompt_key=req.prompt_key)
        enabled = bool(cfg.get("enabled", False))
        if not enabled:
            return choose_variant(
                prompt_key=req.prompt_key,
                bucket_key=req.bucket_key,
                variants=variants,
                variant_hint=req.variant_hint,
            )

        prefixes = cfg.get("segment_prefixes")
        if isinstance(prefixes, list) and prefixes:
            if not any(str(req.bucket_key).startswith(str(p)) for p in prefixes):
                return choose_variant(
                    prompt_key=req.prompt_key,
                    bucket_key=req.bucket_key,
                    variants=variants,
                    variant_hint=req.variant_hint,
                )

        rollout_percentage = int(cfg.get("rollout_percentage", 100))
        if rollout_percentage < 100:
            digest = hashlib.sha256(f"{req.prompt_key}:{req.bucket_key}:rollout".encode("utf-8")).hexdigest()
            pos = int(digest[:8], 16) % 100
            if pos >= rollout_percentage:
                return choose_variant(
                    prompt_key=req.prompt_key,
                    bucket_key=req.bucket_key,
                    variants=variants,
                    variant_hint=req.variant_hint,
                )

        weights = cfg.get("variant_weights")
        if not isinstance(weights, dict):
            weights = {}
        sticky_salt = str(cfg.get("sticky_salt", ""))
        return choose_variant_with_weights(
            prompt_key=req.prompt_key,
            bucket_key=req.bucket_key,
            variants=variants,
            variant_weights={str(k): int(v) for k, v in weights.items()},
            variant_hint=req.variant_hint,
            sticky_salt=sticky_salt,
        )

    async def _run_pull_reconciler(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._pull_interval_seconds)
                report = await self.refresh()
                if report.invalidated_assets > 0:
                    logger.info(
                        "prompt_pull_reconcile",
                        extra={
                            "extra_fields": {
                                "checked_assets": report.checked_assets,
                                "invalidated_assets": report.invalidated_assets,
                            }
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"prompt pull reconcile failed: {exc}")

    async def _run_bus_listener(self) -> None:
        if self._bus is None:
            return
        while True:
            try:
                event = await self._bus.recv()
                if event is None:
                    await asyncio.sleep(0.2)
                    continue
                await self.invalidate(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"prompt bus listener failed: {exc}")

    @staticmethod
    def _with_cache_level(value: PromptResolveResult, cache_level: str) -> PromptResolveResult:
        cloned = copy.deepcopy(value)
        return PromptResolveResult(
            messages=cloned.messages,
            asset_version=cloned.asset_version,
            variant_id=cloned.variant_id,
            token_estimate=cloned.token_estimate,
            cache_level=cache_level,  # type: ignore[arg-type]
        )


def build_default_prompt_provider() -> PromptProviderService:
    source = FilePromptSource(base_dir=Path(settings.prompts_dir).resolve())
    l1 = InMemoryPromptCache(ttl_seconds=settings.prompt_l1_ttl_seconds, swr_seconds=settings.prompt_l1_swr_seconds)
    l2 = (
        RedisPromptCache(redis_url=settings.redis_url, key_prefix=settings.prompt_l2_key_prefix)
        if settings.prompt_l2_cache_enabled
        else NoopPromptCache()
    )
    bus = None
    if settings.prompt_invalidation_bus_enabled:
        from src.prompts.invalidation_redis import RedisInvalidationBus

        bus = RedisInvalidationBus(redis_url=settings.redis_url, channel=settings.prompt_invalidation_channel)
    return PromptProviderService(
        source=source,
        l1_cache=l1,
        l2_cache=l2,
        invalidation_bus=bus,
        pull_interval_seconds=settings.prompt_pull_interval_seconds,
        l2_ttl_seconds=settings.prompt_l2_ttl_seconds,
        l1_ttl_seconds=settings.prompt_l1_ttl_seconds,
    )


def get_prompt_provider() -> PromptProviderService:
    global _DEFAULT_PROMPT_PROVIDER
    if _DEFAULT_PROMPT_PROVIDER is None:
        _DEFAULT_PROMPT_PROVIDER = build_default_prompt_provider()
    return _DEFAULT_PROMPT_PROVIDER
