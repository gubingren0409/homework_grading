import logging
import sqlite3
from typing import Any, Dict, List, Optional, Sequence

import aiosqlite

from src.db.core_utils import _execute_write_with_retry, _open_connection
from src.db.dao._migrations import _ensure_domain_split_tables

logger = logging.getLogger(__name__)


async def upsert_teacher_review_decision(
    db_path: str,
    *,
    task_id: str,
    sample_id: str,
    student_id: Optional[str],
    decision: str,
    final_score: Optional[float],
    teacher_comment: str,
    include_in_dataset: bool,
) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_domain_split_tables(db)
        await db.execute(
            """
            INSERT INTO teacher_review_decisions (
                task_id, sample_id, student_id, decision, final_score, teacher_comment, include_in_dataset
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, sample_id) DO UPDATE SET
                student_id = excluded.student_id,
                decision = excluded.decision,
                final_score = excluded.final_score,
                teacher_comment = excluded.teacher_comment,
                include_in_dataset = excluded.include_in_dataset,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                task_id,
                sample_id,
                student_id,
                decision,
                float(final_score) if final_score is not None else None,
                teacher_comment,
                1 if include_in_dataset else 0,
            ),
        )

    await _execute_write_with_retry(db_path, _op)


async def list_teacher_review_decisions(
    db_path: str,
    *,
    task_id: Optional[str] = None,
    sample_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_domain_split_tables(db)
        db.row_factory = aiosqlite.Row
        sql = (
            "SELECT task_id, sample_id, student_id, decision, final_score, teacher_comment, "
            "include_in_dataset, created_at, updated_at "
            "FROM teacher_review_decisions WHERE 1=1"
        )
        params: List[Any] = []
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if sample_id:
            sql += " AND sample_id = ?"
            params.append(sample_id)
        sql += " ORDER BY updated_at DESC, task_id DESC, sample_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_teacher_review_decision_counts(
    db_path: str,
    *,
    task_ids: Sequence[str],
) -> Dict[str, int]:
    normalized = [str(x).strip() for x in task_ids if str(x).strip()]
    if not normalized:
        return {}
    placeholders = ",".join("?" for _ in normalized)
    async with _open_connection(db_path) as db:
        await _ensure_domain_split_tables(db)
        async with db.execute(
            f"""
            SELECT task_id, COUNT(1) AS cnt
            FROM teacher_review_decisions
            WHERE task_id IN ({placeholders})
            GROUP BY task_id
            """,
            tuple(normalized),
        ) as cursor:
            rows = await cursor.fetchall()
            return {str(task_id): int(cnt) for task_id, cnt in rows}
