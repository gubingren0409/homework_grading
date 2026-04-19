import logging
import sqlite3
from typing import Any, Dict, List, Optional

import aiosqlite

from src.db.core_utils import _execute_write_with_retry, _open_connection
from src.db.json_utils import _to_json_string
from src.db.dao._migrations import _ensure_rubrics_schema, _ensure_rubric_audit_schema

logger = logging.getLogger(__name__)


async def save_rubric(
    db_path: str,
    rubric_id: str,
    question_id: Optional[str],
    rubric_json: Any,
    *,
    source_fingerprint: Optional[str] = None,
) -> None:
    rubric_str = _to_json_string(rubric_json)

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_rubrics_schema(db)
        await db.execute(
            """
            INSERT INTO rubrics (rubric_id, question_id, rubric_json, source_fingerprint)
            VALUES (?, ?, ?, ?)
            """,
            (rubric_id, question_id, rubric_str, source_fingerprint),
        )

    await _execute_write_with_retry(db_path, _op)


async def get_rubric(db_path: str, rubric_id: str) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_rubrics_schema(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT rubric_id, question_id, rubric_json, created_at FROM rubrics WHERE rubric_id = ?",
            (rubric_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_rubrics(
    db_path: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_rubrics_schema(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT rubric_id, question_id, created_at
            FROM rubrics
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_recent_rubric_by_fingerprint(
    db_path: str,
    *,
    source_fingerprint: str,
    within_seconds: int,
) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_rubrics_schema(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT rubric_id, question_id, created_at
            FROM rubrics
            WHERE source_fingerprint = ?
              AND created_at >= datetime('now', '-' || ? || ' seconds')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (source_fingerprint, within_seconds),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def append_rubric_generate_audit(
    db_path: str,
    *,
    trace_id: str,
    rubric_id: Optional[str],
    source_fingerprint: str,
    reused_from_cache: bool,
    force_regenerate: bool,
    source_file_count: int,
    client_ip: Optional[str],
    user_agent: Optional[str],
    referer: Optional[str],
) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_rubric_audit_schema(db)
        await db.execute(
            """
            INSERT INTO rubric_generate_audit (
                trace_id, rubric_id, source_fingerprint, reused_from_cache, force_regenerate,
                source_file_count, client_ip, user_agent, referer
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                rubric_id,
                source_fingerprint,
                1 if reused_from_cache else 0,
                1 if force_regenerate else 0,
                source_file_count,
                client_ip,
                user_agent,
                referer,
            ),
        )

    await _execute_write_with_retry(db_path, _op)


async def list_rubric_generate_audit(
    db_path: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_rubric_audit_schema(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, trace_id, rubric_id, source_fingerprint, reused_from_cache, force_regenerate,
                   source_file_count, client_ip, user_agent, referer, created_at
            FROM rubric_generate_audit
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
