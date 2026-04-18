import json
import logging
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Sequence

import aiosqlite

from src.db.core_utils import _execute_write_with_retry, _open_connection
from src.db.json_utils import _to_json_string
from src.db.dao._migrations import (
    _ensure_runtime_telemetry_table,
    _ensure_prompt_control_tables,
    _ensure_domain_split_tables,
)

logger = logging.getLogger(__name__)


async def get_annotation_dataset_stats(
    db_path: str,
    *,
    lookback_hours: int = 24,
) -> Dict[str, int]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(
                """
                SELECT
                    COUNT(1) AS total_assets,
                    SUM(CASE WHEN is_integrated_to_dataset = 1 THEN 1 ELSE 0 END) AS integrated_assets
                FROM golden_annotation_assets
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                row = await cursor.fetchone()
                total_assets = int((row[0] if row else 0) or 0)
                integrated_assets = int((row[1] if row else 0) or 0)
                return {
                    "total_assets": total_assets,
                    "integrated_assets": integrated_assets,
                    "pending_assets": max(total_assets - integrated_assets, 0),
                }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {"total_assets": 0, "integrated_assets": 0, "pending_assets": 0}
            raise


async def get_review_queue_stats(
    db_path: str,
    *,
    lookback_hours: int = 24,
) -> Dict[str, int]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(
                """
                SELECT
                    SUM(CASE WHEN review_status = 'PENDING_REVIEW' THEN 1 ELSE 0 END) AS pending_review_count,
                    SUM(CASE WHEN review_status = 'REVIEWED' THEN 1 ELSE 0 END) AS reviewed_count
                FROM tasks
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                row = await cursor.fetchone()
                pending_review = int((row[0] if row else 0) or 0)
                reviewed = int((row[1] if row else 0) or 0)
                return {
                    "pending_review_count": pending_review,
                    "reviewed_count": reviewed,
                }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {"pending_review_count": 0, "reviewed_count": 0}
            raise


async def get_prompt_cache_level_stats(
    db_path: str,
    *,
    lookback_hours: int = 24,
) -> Dict[str, int]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(
                """
                SELECT
                    SUM(CASE WHEN prompt_cache_level = 'L1' THEN 1 ELSE 0 END) AS l1_count,
                    SUM(CASE WHEN prompt_cache_level = 'L2' THEN 1 ELSE 0 END) AS l2_count,
                    SUM(CASE WHEN prompt_cache_level = 'LKG' THEN 1 ELSE 0 END) AS lkg_count,
                    SUM(CASE WHEN prompt_cache_level = 'SOURCE' THEN 1 ELSE 0 END) AS source_count
                FROM task_runtime_telemetry
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                row = await cursor.fetchone()
                return {
                    "l1_count": int((row[0] if row else 0) or 0),
                    "l2_count": int((row[1] if row else 0) or 0),
                    "lkg_count": int((row[2] if row else 0) or 0),
                    "source_count": int((row[3] if row else 0) or 0),
                }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {"l1_count": 0, "l2_count": 0, "lkg_count": 0, "source_count": 0}
            raise


async def upsert_task_runtime_telemetry(
    db_path: str,
    *,
    task_id: str,
    trace_id: str,
    requested_model: str,
    model_used: str,
    route_reason: str,
    fallback_used: bool,
    fallback_reason: Optional[str],
    prompt_key: str,
    prompt_asset_version: str,
    prompt_variant_id: str,
    prompt_cache_level: str,
    prompt_token_estimate: int,
    succeeded: bool,
) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_runtime_telemetry_table(db)
        await db.execute(
            """
            INSERT INTO task_runtime_telemetry (
                task_id, trace_id, requested_model, model_used, route_reason, fallback_used, fallback_reason,
                prompt_key, prompt_asset_version, prompt_variant_id, prompt_cache_level,
                prompt_token_estimate, succeeded, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(task_id)
            DO UPDATE SET
                trace_id=excluded.trace_id,
                requested_model=excluded.requested_model,
                model_used=excluded.model_used,
                route_reason=excluded.route_reason,
                fallback_used=excluded.fallback_used,
                fallback_reason=excluded.fallback_reason,
                prompt_key=excluded.prompt_key,
                prompt_asset_version=excluded.prompt_asset_version,
                prompt_variant_id=excluded.prompt_variant_id,
                prompt_cache_level=excluded.prompt_cache_level,
                prompt_token_estimate=excluded.prompt_token_estimate,
                succeeded=excluded.succeeded,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                task_id,
                trace_id,
                requested_model,
                model_used,
                route_reason,
                1 if fallback_used else 0,
                fallback_reason,
                prompt_key,
                prompt_asset_version,
                prompt_variant_id,
                prompt_cache_level,
                int(prompt_token_estimate),
                1 if succeeded else 0,
            ),
        )

    await _execute_write_with_retry(db_path, _op)


async def get_runtime_telemetry_model_hits(
    db_path: str,
    *,
    lookback_hours: int = 24,
) -> Dict[str, int]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(
                """
                SELECT model_used, COUNT(1) AS cnt
                FROM task_runtime_telemetry
                WHERE created_at >= datetime('now', ?)
                GROUP BY model_used
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                rows = await cursor.fetchall()
                return {str(model): int(cnt) for model, cnt in rows}
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {}
            raise


async def get_runtime_telemetry_fallback_stats(
    db_path: str,
    *,
    lookback_hours: int = 24,
) -> Dict[str, Any]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(
                """
                SELECT
                    COUNT(1) AS total_count,
                    SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) AS fallback_count
                FROM task_runtime_telemetry
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                row = await cursor.fetchone()
                total_count = int((row[0] if row else 0) or 0)
                fallback_count = int((row[1] if row else 0) or 0)

            async with db.execute(
                """
                SELECT route_reason, COUNT(1) AS cnt
                FROM task_runtime_telemetry
                WHERE created_at >= datetime('now', ?)
                GROUP BY route_reason
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                reason_rows = await cursor.fetchall()
                reason_hits = {str(reason): int(cnt) for reason, cnt in reason_rows}

            fallback_rate = (float(fallback_count) / float(total_count)) if total_count > 0 else 0.0
            return {
                "total_count": total_count,
                "fallback_count": fallback_count,
                "fallback_rate": fallback_rate,
                "reason_hits": reason_hits,
            }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {"total_count": 0, "fallback_count": 0, "fallback_rate": 0.0, "reason_hits": {}}
            raise


async def upsert_prompt_control_state(
    db_path: str,
    *,
    prompt_key: str,
    forced_variant_id: Optional[str],
    lkg_mode: bool,
) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_prompt_control_tables(db)
        await db.execute(
            """
            INSERT INTO prompt_control_state (prompt_key, forced_variant_id, lkg_mode, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(prompt_key)
            DO UPDATE SET
                forced_variant_id=excluded.forced_variant_id,
                lkg_mode=excluded.lkg_mode,
                updated_at=CURRENT_TIMESTAMP
            """,
            (prompt_key, forced_variant_id, 1 if lkg_mode else 0),
        )

    await _execute_write_with_retry(db_path, _op)


async def get_prompt_control_state(
    db_path: str,
    *,
    prompt_key: str,
) -> Dict[str, Any]:
    async with _open_connection(db_path) as db:
        try:
            await _ensure_prompt_control_tables(db)
            async with db.execute(
                """
                SELECT prompt_key, forced_variant_id, lkg_mode, updated_at
                FROM prompt_control_state
                WHERE prompt_key = ?
                """,
                (prompt_key,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return {
                        "prompt_key": prompt_key,
                        "forced_variant_id": None,
                        "lkg_mode": False,
                        "updated_at": None,
                    }
                return {
                    "prompt_key": str(row[0]),
                    "forced_variant_id": row[1],
                    "lkg_mode": bool(row[2]),
                    "updated_at": row[3],
                }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {
                    "prompt_key": prompt_key,
                    "forced_variant_id": None,
                    "lkg_mode": False,
                    "updated_at": None,
                }
            raise


async def upsert_prompt_ab_config(
    db_path: str,
    *,
    prompt_key: str,
    enabled: bool,
    rollout_percentage: int,
    variant_weights: Mapping[str, int],
    segment_prefixes: Sequence[str],
    sticky_salt: str,
) -> None:
    weights_json = _to_json_string(dict(variant_weights))
    segments_json = _to_json_string(list(segment_prefixes))

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_prompt_control_tables(db)
        await db.execute(
            """
            INSERT INTO prompt_ab_configs (
                prompt_key, enabled, rollout_percentage, variant_weights_json,
                segment_prefixes_json, sticky_salt, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(prompt_key)
            DO UPDATE SET
                enabled=excluded.enabled,
                rollout_percentage=excluded.rollout_percentage,
                variant_weights_json=excluded.variant_weights_json,
                segment_prefixes_json=excluded.segment_prefixes_json,
                sticky_salt=excluded.sticky_salt,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                prompt_key,
                1 if enabled else 0,
                int(rollout_percentage),
                weights_json,
                segments_json,
                sticky_salt,
            ),
        )

    await _execute_write_with_retry(db_path, _op)


async def get_prompt_ab_config(
    db_path: str,
    *,
    prompt_key: str,
) -> Dict[str, Any]:
    async with _open_connection(db_path) as db:
        try:
            await _ensure_prompt_control_tables(db)
            async with db.execute(
                """
                SELECT
                    prompt_key, enabled, rollout_percentage, variant_weights_json,
                    segment_prefixes_json, sticky_salt, updated_at
                FROM prompt_ab_configs
                WHERE prompt_key = ?
                """,
                (prompt_key,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return {
                        "prompt_key": prompt_key,
                        "enabled": False,
                        "rollout_percentage": 100,
                        "variant_weights": {},
                        "segment_prefixes": [],
                        "sticky_salt": "",
                        "updated_at": None,
                    }
                variant_weights_raw = row[3]
                segment_prefixes_raw = row[4]
                try:
                    variant_weights = json.loads(variant_weights_raw) if isinstance(variant_weights_raw, str) else {}
                    if not isinstance(variant_weights, dict):
                        variant_weights = {}
                except Exception:
                    variant_weights = {}
                try:
                    segment_prefixes = json.loads(segment_prefixes_raw) if isinstance(segment_prefixes_raw, str) else []
                    if not isinstance(segment_prefixes, list):
                        segment_prefixes = []
                except Exception:
                    segment_prefixes = []
                return {
                    "prompt_key": str(row[0]),
                    "enabled": bool(row[1]),
                    "rollout_percentage": int(row[2]),
                    "variant_weights": {str(k): int(v) for k, v in variant_weights.items()},
                    "segment_prefixes": [str(x) for x in segment_prefixes],
                    "sticky_salt": str(row[5] or ""),
                    "updated_at": row[6],
                }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {
                    "prompt_key": prompt_key,
                    "enabled": False,
                    "rollout_percentage": 100,
                    "variant_weights": {},
                    "segment_prefixes": [],
                    "sticky_salt": "",
                    "updated_at": None,
                }
            raise


async def append_prompt_ops_audit(
    db_path: str,
    *,
    trace_id: str,
    operator_id: Optional[str],
    action: str,
    prompt_key: Optional[str],
    payload_json: Any,
) -> None:
    payload = _to_json_string(payload_json)

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_prompt_control_tables(db)
        await db.execute(
            """
            INSERT INTO prompt_ops_audit_log
            (trace_id, operator_id, action, prompt_key, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trace_id, operator_id, action, prompt_key, payload),
        )

    await _execute_write_with_retry(db_path, _op)


async def list_prompt_ops_audit(
    db_path: str,
    *,
    prompt_key: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        try:
            await _ensure_prompt_control_tables(db)
            db.row_factory = aiosqlite.Row
            sql = (
                "SELECT id, trace_id, operator_id, action, prompt_key, payload_json, created_at "
                "FROM prompt_ops_audit_log WHERE 1=1"
            )
            params: List[Any] = []
            if prompt_key:
                sql += " AND prompt_key = ?"
                params.append(prompt_key)
            sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            async with db.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return []
            raise


async def get_ops_feature_flags(db_path: str) -> Dict[str, Any]:
    async with _open_connection(db_path) as db:
        try:
            await _ensure_prompt_control_tables(db)
            async with db.execute(
                """
                SELECT deployment_environment, provider_switch_enabled, prompt_control_enabled, router_control_enabled, updated_at
                FROM ops_feature_flags
                WHERE id = 1
                """
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return {
                        "deployment_environment": "dev",
                        "provider_switch_enabled": True,
                        "prompt_control_enabled": True,
                        "router_control_enabled": True,
                        "updated_at": None,
                    }
                return {
                    "deployment_environment": str(row[0]),
                    "provider_switch_enabled": bool(row[1]),
                    "prompt_control_enabled": bool(row[2]),
                    "router_control_enabled": bool(row[3]),
                    "updated_at": row[4],
                }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {
                    "deployment_environment": "dev",
                    "provider_switch_enabled": True,
                    "prompt_control_enabled": True,
                    "router_control_enabled": True,
                    "updated_at": None,
                }
            raise


async def upsert_ops_feature_flags(
    db_path: str,
    *,
    deployment_environment: str,
    provider_switch_enabled: bool,
    prompt_control_enabled: bool,
    router_control_enabled: bool,
) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_prompt_control_tables(db)
        await db.execute(
            """
            INSERT INTO ops_feature_flags (
                id, deployment_environment, provider_switch_enabled, prompt_control_enabled, router_control_enabled, updated_at
            )
            VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id)
            DO UPDATE SET
                deployment_environment=excluded.deployment_environment,
                provider_switch_enabled=excluded.provider_switch_enabled,
                prompt_control_enabled=excluded.prompt_control_enabled,
                router_control_enabled=excluded.router_control_enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                deployment_environment,
                1 if provider_switch_enabled else 0,
                1 if prompt_control_enabled else 0,
                1 if router_control_enabled else 0,
            ),
        )

    await _execute_write_with_retry(db_path, _op)


async def list_ops_release_controls(db_path: str) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_prompt_control_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT layer, strategy, rollout_percentage, target_version, config_json, rollback_config_json, updated_at
            FROM ops_release_controls
            ORDER BY CASE layer WHEN 'api' THEN 0 WHEN 'prompt' THEN 1 WHEN 'router' THEN 2 ELSE 9 END
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_ops_release_control(
    db_path: str,
    *,
    layer: str,
) -> Dict[str, Any]:
    async with _open_connection(db_path) as db:
        await _ensure_prompt_control_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT layer, strategy, rollout_percentage, target_version, config_json, rollback_config_json, updated_at
            FROM ops_release_controls
            WHERE layer = ?
            """,
            (layer,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return {
                    "layer": layer,
                    "strategy": "stable",
                    "rollout_percentage": 100,
                    "target_version": None,
                    "config_json": "{}",
                    "rollback_config_json": "{}",
                    "updated_at": None,
                }
            return dict(row)


async def upsert_ops_release_control(
    db_path: str,
    *,
    layer: str,
    strategy: str,
    rollout_percentage: int,
    target_version: Optional[str],
    config_json: Any,
    rollback_config_json: Any,
) -> None:
    config_payload = _to_json_string(config_json)
    rollback_payload = _to_json_string(rollback_config_json)

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_prompt_control_tables(db)
        await db.execute(
            """
            INSERT INTO ops_release_controls (
                layer, strategy, rollout_percentage, target_version, config_json, rollback_config_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(layer)
            DO UPDATE SET
                strategy=excluded.strategy,
                rollout_percentage=excluded.rollout_percentage,
                target_version=excluded.target_version,
                config_json=excluded.config_json,
                rollback_config_json=excluded.rollback_config_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                layer,
                strategy,
                int(rollout_percentage),
                target_version,
                config_payload,
                rollback_payload,
            ),
        )

    await _execute_write_with_retry(db_path, _op)


async def append_ops_fault_drill_report(
    db_path: str,
    *,
    drill_type: str,
    status: str,
    details_json: Any,
    operator_id: Optional[str],
) -> int:
    details_payload = _to_json_string(details_json)
    report_id: int = 0

    async def _op(db: aiosqlite.Connection) -> None:
        nonlocal report_id
        await _ensure_prompt_control_tables(db)
        cursor = await db.execute(
            """
            INSERT INTO ops_fault_drill_reports
            (drill_type, status, details_json, operator_id)
            VALUES (?, ?, ?, ?)
            """,
            (drill_type, status, details_payload, operator_id),
        )
        report_id = int(cursor.lastrowid or 0)

    await _execute_write_with_retry(db_path, _op)
    return report_id


async def get_ops_fault_drill_report_by_id(
    db_path: str,
    *,
    report_id: int,
) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_prompt_control_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, drill_type, status, details_json, operator_id, created_at
            FROM ops_fault_drill_reports
            WHERE id = ?
            """,
            (report_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_ops_fault_drill_reports(
    db_path: str,
    *,
    drill_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_prompt_control_tables(db)
        db.row_factory = aiosqlite.Row
        sql = (
            "SELECT id, drill_type, status, details_json, operator_id, created_at "
            "FROM ops_fault_drill_reports WHERE 1=1"
        )
        params: List[Any] = []
        if drill_type:
            sql += " AND drill_type = ?"
            params.append(drill_type)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
