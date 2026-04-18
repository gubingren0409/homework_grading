import logging
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Sequence, Mapping

import aiosqlite

from src.db.core_utils import _execute_write_with_retry, _open_connection
from src.db.json_utils import _to_json_string
from src.db.dao._migrations import _ensure_tasks_columns, _ensure_skill_validation_tables, _STATUS_WEIGHTS

logger = logging.getLogger(__name__)


async def create_task(
    db_path: str,
    task_id: str,
    *,
    submitted_count: int = 0,
) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=False)
        await db.execute(
            "INSERT INTO tasks (task_id, status, submitted_count, progress, eta_seconds) VALUES (?, ?, ?, ?, ?)",
            (task_id, "PENDING", max(0, int(submitted_count)), 0.0, 60),
        )

    await _execute_write_with_retry(db_path, _op)


async def update_task_progress(
    db_path: str,
    task_id: str,
    *,
    progress: float,
    eta_seconds: Optional[int] = None,
) -> None:
    bounded_progress = float(progress)
    if bounded_progress < 0.0:
        bounded_progress = 0.0
    if bounded_progress > 1.0:
        bounded_progress = 1.0
    eta_value: Optional[int] = None
    if eta_seconds is not None:
        eta_value = max(0, int(eta_seconds))

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=False)
        await db.execute(
            """
            UPDATE tasks
            SET progress = ?, eta_seconds = ?, updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
              AND status IN ('PENDING', 'PROCESSING')
            """,
            (bounded_progress, eta_value, task_id),
        )

    await _execute_write_with_retry(db_path, _op)


async def set_task_rubric_id(db_path: str, task_id: str, rubric_id: str) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=False)
        await db.execute(
            "UPDATE tasks SET rubric_id = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (rubric_id, task_id),
        )

    await _execute_write_with_retry(db_path, _op)


async def set_task_review_status(db_path: str, task_id: str, review_status: str) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=False)
        await db.execute(
            "UPDATE tasks SET review_status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (review_status, task_id),
        )

    await _execute_write_with_retry(db_path, _op)


async def update_task_status(
    db_path: str,
    task_id: str,
    status: str,
    error: Optional[str] = None,
    *,
    grading_status: Optional[str] = None,
    review_status: Optional[str] = None,
    fallback_reason: Optional[str] = None,
) -> None:
    new_weight = _STATUS_WEIGHTS.get(status)
    if new_weight is None:
        raise ValueError(f"Unsupported status transition target: {status}")

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=False)
        assignments = [
            "status = ?",
            "error_message = ?",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params: List[Any] = [status, error]
        if status == "PENDING":
            assignments.append("progress = ?")
            assignments.append("eta_seconds = ?")
            params.extend([0.0, 60])
        elif status == "PROCESSING":
            assignments.append("progress = ?")
            assignments.append("eta_seconds = ?")
            params.extend([0.1, 60])
        elif status == "COMPLETED":
            assignments.append("progress = ?")
            assignments.append("eta_seconds = ?")
            params.extend([1.0, 0])
        elif status == "FAILED":
            assignments.append("progress = ?")
            assignments.append("eta_seconds = ?")
            params.extend([1.0, 0])
        if review_status is not None:
            assignments.append("review_status = ?")
            params.append(review_status)
        if grading_status is not None:
            assignments.append("grading_status = ?")
            params.append(grading_status)
        if fallback_reason is not None:
            assignments.append("fallback_reason = ?")
            params.append(fallback_reason)
        params.extend([task_id, new_weight])
        sql = (
            f"UPDATE tasks SET {', '.join(assignments)} "
            "WHERE task_id = ? "
            "AND (CASE status "
            "WHEN 'PENDING' THEN 0 "
            "WHEN 'PROCESSING' THEN 1 "
            "WHEN 'COMPLETED' THEN 2 "
            "WHEN 'FAILED' THEN 3 "
            "ELSE -1 END) < ?"
        )
        cursor = await db.execute(sql, params)
        if cursor.rowcount == 0:
            logger.warning(
                "task_status_transition_rejected",
                extra={
                    "extra_fields": {
                        "task_id": task_id,
                        "target_status": status,
                    }
                },
            )

    await _execute_write_with_retry(db_path, _op)


async def update_task_celery_id(
    db_path: str,
    task_id: str,
    celery_task_id: str,
) -> None:
    """Phase 28: Update Celery task ID for potential revocation."""
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=True)
        await db.execute(
            "UPDATE tasks SET celery_task_id = ? WHERE task_id = ?",
            (celery_task_id, task_id),
        )

    await _execute_write_with_retry(db_path, _op)


async def get_task(db_path: str, task_id: str) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_pending_review_tasks(
    db_path: str,
    *,
    grading_status_filter: Optional[str] = None,
    task_id: Optional[str] = None,
    order_by: str = "updated_at",
    order_direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        sql = (
            "SELECT task_id, status, grading_status, rubric_id, review_status, error_message, fallback_reason, "
            "created_at, updated_at "
            "FROM tasks WHERE review_status = 'PENDING_REVIEW'"
        )
        params: List[Any] = []
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if grading_status_filter:
            sql += " AND grading_status = ?"
            params.append(grading_status_filter)
        order_candidates = {
            "updated_at": "updated_at",
            "created_at": "created_at",
            "task_id": "task_id",
        }
        normalized_order = order_candidates.get(str(order_by).strip().lower(), "updated_at")
        normalized_direction = "ASC" if str(order_direction).strip().lower() == "asc" else "DESC"
        sql += f" ORDER BY {normalized_order} {normalized_direction}, task_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def list_pending_review_task_rows(
    db_path: str,
    *,
    grading_status_filter: Optional[str] = None,
    task_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        sql = (
            "SELECT task_id, status, grading_status, rubric_id, review_status, submitted_count, "
            "error_message, fallback_reason, created_at, updated_at "
            "FROM tasks WHERE review_status = 'PENDING_REVIEW'"
        )
        params: List[Any] = []
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        if grading_status_filter:
            sql += " AND grading_status = ?"
            params.append(grading_status_filter)
        sql += " ORDER BY updated_at DESC, task_id DESC"
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_task_status_counts(db_path: str) -> Dict[str, int]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute("SELECT status, COUNT(1) AS cnt FROM tasks GROUP BY status") as cursor:
                rows = await cursor.fetchall()
                result: Dict[str, int] = {}
                for status, cnt in rows:
                    result[str(status)] = int(cnt)
                return result
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {}
            raise


async def get_task_statuses_by_celery_ids(
    db_path: str,
    *,
    celery_task_ids: Sequence[str],
) -> Dict[str, str]:
    normalized_ids = [str(x).strip() for x in celery_task_ids if str(x).strip()]
    if not normalized_ids:
        return {}
    placeholders = ",".join(["?"] * len(normalized_ids))
    query = f"SELECT celery_task_id, status FROM tasks WHERE celery_task_id IN ({placeholders})"
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(query, tuple(normalized_ids)) as cursor:
                rows = await cursor.fetchall()
                return {str(celery_task_id): str(status) for celery_task_id, status in rows}
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {}
            raise


async def list_processing_tasks(
    db_path: str,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, int(limit))
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                """
                SELECT
                    task_id,
                    celery_task_id,
                    progress,
                    eta_seconds,
                    created_at,
                    updated_at,
                    CAST((julianday('now') - julianday(updated_at)) * 86400 AS INTEGER) AS age_seconds
                FROM tasks
                WHERE status = 'PROCESSING'
                ORDER BY updated_at DESC, task_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return []
            raise


async def list_stale_pending_tasks(
    db_path: str,
    *,
    timeout_seconds: int = 900,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    safe_timeout = max(1, int(timeout_seconds))
    safe_limit = max(1, int(limit))
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                """
                SELECT
                    task_id,
                    celery_task_id,
                    progress,
                    eta_seconds,
                    created_at,
                    updated_at,
                    CAST((julianday('now') - julianday(created_at)) * 86400 AS INTEGER) AS age_seconds
                FROM tasks
                WHERE status = 'PENDING'
                  AND (julianday('now') - julianday(created_at)) * 86400 > ?
                ORDER BY created_at ASC, task_id ASC
                LIMIT ?
                """,
                (safe_timeout, safe_limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return []
            raise


async def fail_stale_pending_orphan_tasks(
    db_path: str,
    *,
    timeout_seconds: int = 900,
    limit: int = 200,
) -> List[str]:
    """
    Mark stale PENDING tasks as FAILED when they are likely orphaned.

    Orphan heuristics:
    - celery_task_id is NULL/blank
    - local fallback id prefix: "local:"
    - synthetic test id: "mock-celery-id"
    """
    safe_timeout = max(1, int(timeout_seconds))
    safe_limit = max(1, int(limit))
    cleaned_task_ids: List[str] = []

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=True)
        async with db.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE status = 'PENDING'
              AND (julianday('now') - julianday(created_at)) * 86400 > ?
              AND (
                    celery_task_id IS NULL
                    OR TRIM(celery_task_id) = ''
                    OR celery_task_id LIKE 'local:%'
                    OR celery_task_id = 'mock-celery-id'
              )
            ORDER BY created_at ASC, task_id ASC
            LIMIT ?
            """,
            (safe_timeout, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()

        task_ids = [str(row[0]) for row in rows]
        if not task_ids:
            return

        placeholders = ",".join("?" for _ in task_ids)
        error_message = (
            f"Queue timeout: stale pending task exceeded {safe_timeout}s "
            "without worker pickup (orphan cleanup)"
        )
        await db.execute(
            f"""
            UPDATE tasks
            SET status = 'FAILED',
                error_message = ?,
                fallback_reason = ?,
                progress = 1.0,
                eta_seconds = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'PENDING'
              AND task_id IN ({placeholders})
            """,
            (error_message, "QUEUE_STALE_ORPHAN", *task_ids),
        )
        cleaned_task_ids.extend(task_ids)

    await _execute_write_with_retry(db_path, _op)
    return cleaned_task_ids


async def get_completion_latencies_seconds(
    db_path: str,
    *,
    lookback_hours: int = 24,
) -> List[float]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(
                """
                SELECT
                    (julianday(MIN(gr.created_at)) - julianday(t.created_at)) * 86400.0 AS completion_seconds
                FROM tasks t
                JOIN grading_results gr ON gr.task_id = t.task_id
                WHERE t.status = 'COMPLETED'
                  AND t.created_at >= datetime('now', ?)
                GROUP BY t.task_id
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                rows = await cursor.fetchall()
                latencies: List[float] = []
                for (seconds,) in rows:
                    try:
                        value = float(seconds)
                    except (TypeError, ValueError):
                        continue
                    if value >= 0:
                        latencies.append(value)
                return latencies
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return []
            raise


async def get_task_volume_stats(
    db_path: str,
    *,
    lookback_hours: int = 24,
) -> Dict[str, int]:
    async with _open_connection(db_path) as db:
        try:
            async with db.execute(
                """
                SELECT
                    COUNT(1) AS total_count,
                    SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed_count,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed_count
                FROM tasks
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{int(lookback_hours)} hours",),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return {"total_count": 0, "completed_count": 0, "failed_count": 0}
                total_count = int(row[0] or 0)
                completed_count = int(row[1] or 0)
                failed_count = int(row[2] or 0)
                return {
                    "total_count": total_count,
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                }
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if "no such table" in str(exc).lower():
                return {"total_count": 0, "completed_count": 0, "failed_count": 0}
            raise


async def insert_skill_validation_records(
    db_path: str,
    records: Sequence[Mapping[str, Any]],
) -> int:
    if not records:
        return 0

    rows_to_insert: List[tuple[Any, ...]] = []
    for record in records:
        task_id = str(record.get("task_id") or "").strip()
        student_id = str(record.get("student_id") or "").strip()
        checker = str(record.get("checker") or "").strip()
        status = str(record.get("status") or "").strip().lower()
        if not task_id:
            raise ValueError("skill validation record requires task_id")
        if not student_id:
            raise ValueError("skill validation record requires student_id")
        if not checker:
            raise ValueError("skill validation record requires checker")
        if status not in {"ok", "mismatch", "error"}:
            raise ValueError(f"invalid skill validation status: {status}")

        confidence = float(record.get("confidence", 0.0))
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("skill validation confidence must be within [0, 1]")

        details_json = _to_json_string(record.get("details_json", {}))
        rows_to_insert.append(
            (
                task_id,
                student_id,
                record.get("question_id"),
                checker,
                status,
                confidence,
                details_json,
            )
        )

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_skill_validation_tables(db)
        await db.executemany(
            """
            INSERT INTO skill_validation_records
            (task_id, student_id, question_id, checker, status, confidence, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )

    await _execute_write_with_retry(db_path, _op)
    return len(rows_to_insert)
