from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any, Dict, Literal

import redis.asyncio as aioredis

from src.api.sse import _get_redis_client, get_task_channel
from src.core.config import settings
from src.core.runtime_router import get_runtime_router_controller
from src.db.client import _open_connection


DrillType = Literal["redis_unavailable", "model_failure", "sse_disconnect", "db_pressure"]


async def run_fault_drill(*, drill_type: DrillType, db_path: str) -> Dict[str, Any]:
    if drill_type == "redis_unavailable":
        return await _run_redis_unavailable_drill()
    if drill_type == "model_failure":
        return _run_model_failure_drill()
    if drill_type == "sse_disconnect":
        return await _run_sse_disconnect_drill()
    if drill_type == "db_pressure":
        return await _run_db_pressure_drill(db_path=db_path)
    raise ValueError(f"unsupported drill_type: {drill_type}")


async def _run_redis_unavailable_drill() -> Dict[str, Any]:
    test_url = "redis://127.0.0.1:6399/0"
    start = time.perf_counter()
    error_message = ""
    try:
        probe = aioredis.from_url(
            test_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await probe.ping()
        await probe.aclose()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return {
            "status": "failed",
            "details": {
                "redis_url_tested": test_url,
                "unexpected_result": "reachable",
                "elapsed_ms": round(elapsed_ms, 2),
            },
        }
    except Exception as exc:
        error_message = str(exc)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return {
            "status": "passed",
            "details": {
                "redis_url_tested": test_url,
                "expected_failure_observed": True,
                "error": error_message,
                "elapsed_ms": round(elapsed_ms, 2),
            },
        }


def _run_model_failure_drill() -> Dict[str, Any]:
    controller = get_runtime_router_controller()
    baseline = controller.snapshot()
    for _ in range(max(settings.auto_circuit_min_samples, 20)):
        controller.record_event(
            model=settings.deepseek_model_name,
            success=False,
            token_estimate=1200,
            fallback_used=True,
            reason="failure_rate_threshold",
        )
    decision = controller.decide_cognitive_route(
        readability_status="CLEAR",
        incoming_token_estimate=1100,
        requested_model=settings.deepseek_model_name,
    )
    after = controller.snapshot()
    passed = decision.force_degrade_to_chat and decision.reason in {"failure_rate_threshold", "token_spike_threshold"}
    return {
        "status": "passed" if passed else "failed",
        "details": {
            "baseline_sample_count": int(baseline.get("sample_count", 0)),
            "after_sample_count": int(after.get("sample_count", 0)),
            "decision": {
                "cognitive_model": decision.cognitive_model,
                "force_degrade_to_chat": decision.force_degrade_to_chat,
                "reason": decision.reason,
            },
        },
    }


async def _run_sse_disconnect_drill() -> Dict[str, Any]:
    client = _get_redis_client()
    pubsub = client.pubsub()
    channel = get_task_channel("drill-sse-disconnect")
    message_received = False
    disconnect_closed = False
    try:
        await pubsub.subscribe(channel)
        await client.publish(channel, '{"task_id":"drill-sse-disconnect","status":"PROCESSING"}')
        for _ in range(3):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                message_received = True
                break
            await asyncio.sleep(0.2)
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        disconnect_closed = True
    except Exception as exc:
        return {
            "status": "failed",
            "details": {
                "message_received": message_received,
                "disconnect_closed": disconnect_closed,
                "error": str(exc),
            },
        }
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
    return {
        "status": "passed" if (message_received and disconnect_closed) else "failed",
        "details": {
            "message_received": message_received,
            "disconnect_closed": disconnect_closed,
        },
    }


async def _run_db_pressure_drill(*, db_path: str) -> Dict[str, Any]:
    start = time.perf_counter()

    async def _single_query() -> bool:
        try:
            async with _open_connection(db_path) as db:
                async with db.execute("SELECT COUNT(1) FROM tasks") as cursor:
                    await cursor.fetchone()
            return True
        except (sqlite3.OperationalError, Exception):
            return False

    concurrency = 20
    rounds = 3
    successes = 0
    total = concurrency * rounds
    for _ in range(rounds):
        results = await asyncio.gather(*[_single_query() for _ in range(concurrency)])
        successes += sum(1 for ok in results if ok)

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    success_rate = (float(successes) / float(total)) if total else 0.0
    passed = success_rate >= 0.95
    return {
        "status": "passed" if passed else "failed",
        "details": {
            "queries_total": total,
            "queries_success": successes,
            "success_rate": round(success_rate, 4),
            "elapsed_ms": round(elapsed_ms, 2),
            "concurrency": concurrency,
            "rounds": rounds,
        },
    }
