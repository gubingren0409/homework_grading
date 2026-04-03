import asyncio
import json
import logging
import os
import random
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional, Sequence

import aiosqlite


logger = logging.getLogger(__name__)

# 写操作锁重试参数：指数退避 + 轻微抖动，缓解高并发 "database is locked"。
_WRITE_LOCK_MAX_RETRIES: int = 6
_WRITE_BACKOFF_BASE_SECONDS: float = 0.05
_WRITE_BACKOFF_MAX_SECONDS: float = 1.0
_SQLITE_BUSY_TIMEOUT_MS: int = 5000

_STATUS_WEIGHTS: Dict[str, int] = {
    "PENDING": 0,
    "PROCESSING": 1,
    "COMPLETED": 2,
    "FAILED": 3,
}


async def _apply_connection_pragmas(db: aiosqlite.Connection) -> None:
    """
    对每个连接强制注入并发相关 PRAGMA，避免仅初始化连接生效导致的行为漂移。
    """
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    await db.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS};")


@asynccontextmanager
async def _open_connection(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """
    统一数据库连接入口：确保所有读写连接都带上并发参数。
    """
    db = await aiosqlite.connect(db_path)
    try:
        await _apply_connection_pragmas(db)
        yield db
    finally:
        await db.close()


def _is_lock_error(exc: BaseException) -> bool:
    """
    判定 SQLite 是否因写锁冲突失败。
    """
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def _to_json_compatible(payload: Any) -> Any:
    """
    将 Pydantic 对象/字典/字符串统一转为 JSON 可序列化结构。
    """
    if payload is None:
        return None

    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload

    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        return model_dump()

    if isinstance(payload, (dict, list, int, float, bool)):
        return payload

    raise TypeError(f"Unsupported payload type for JSON serialization: {type(payload)!r}")


def _to_json_string(payload: Any) -> str:
    """
    将输入稳定序列化为 JSON 字符串（保留中文）。
    """
    if isinstance(payload, str):
        try:
            json.loads(payload)
            return payload
        except json.JSONDecodeError:
            return json.dumps(payload, ensure_ascii=False)

    normalized = _to_json_compatible(payload)
    return json.dumps(normalized, ensure_ascii=False)


async def _execute_write_with_retry(
    db_path: str,
    write_operation: Any,
) -> None:
    """
    为 INSERT / UPDATE 等写操作提供锁冲突重试。
    - write_operation 需是 async callable，签名为 (db) -> awaitable
    """
    for attempt in range(1, _WRITE_LOCK_MAX_RETRIES + 1):
        try:
            async with _open_connection(db_path) as db:
                await write_operation(db)
                await db.commit()
                return
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if not _is_lock_error(exc) or attempt == _WRITE_LOCK_MAX_RETRIES:
                raise

            backoff = min(
                _WRITE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                _WRITE_BACKOFF_MAX_SECONDS,
            )
            jitter = random.uniform(0.0, _WRITE_BACKOFF_BASE_SECONDS)
            await asyncio.sleep(backoff + jitter)


async def init_db(db_path: str) -> None:
    """
    初始化数据库结构，并在初始化连接上强制 WAL + NORMAL。
    """
    schema_path = Path(__file__).parent / "schema.sql"
    schema_script = schema_path.read_text(encoding="utf-8")
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    async def _op(db: aiosqlite.Connection) -> None:
        await db.executescript(schema_script)
        await _ensure_tasks_columns(db, include_celery=True)
        await _ensure_rubrics_schema(db)
        await _ensure_domain_split_tables(db)
        await _ensure_skill_validation_tables(db)
        if await _has_legacy_review_columns(db):
            await _migrate_drop_legacy_review_columns(db)

    await _execute_write_with_retry(db_path, _op)


async def _get_tasks_column_names(db: aiosqlite.Connection) -> set[str]:
    async with db.execute("PRAGMA table_info(tasks)") as cursor:
        columns = await cursor.fetchall()
    return {col[1] for col in columns}


async def _ensure_tasks_columns(db: aiosqlite.Connection, *, include_celery: bool) -> None:
    """
    兼容历史数据库：
    - 旧库通过 ALTER TABLE 补齐新增列
    - 在 review_status 可用后创建索引
    """
    column_names = await _get_tasks_column_names(db)

    if include_celery and "celery_task_id" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN celery_task_id TEXT")
        column_names.add("celery_task_id")
    if "rubric_id" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN rubric_id TEXT")
        column_names.add("rubric_id")
    if "grading_status" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN grading_status TEXT")
        column_names.add("grading_status")

    if "review_status" not in column_names:
        await db.execute(
            "ALTER TABLE tasks ADD COLUMN review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'"
        )
        column_names.add("review_status")
    if "fallback_reason" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN fallback_reason TEXT")
        column_names.add("fallback_reason")

    await db.execute("CREATE INDEX IF NOT EXISTS idx_review_status ON tasks(review_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_grading_status ON tasks(grading_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_rubric_id ON tasks(rubric_id)")


async def _ensure_rubrics_schema(db: aiosqlite.Connection) -> None:
    """
    兼容历史数据库：确保 rubrics 表与索引存在。
    """
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS rubrics (
            rubric_id TEXT PRIMARY KEY,
            question_id TEXT,
            rubric_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rubrics_created_at ON rubrics(created_at)")


async def _ensure_domain_split_tables(db: aiosqlite.Connection) -> None:
    """
    Phase 38:
    Ensure physically isolated hygiene + golden asset tables exist for
    data hygiene pipeline and teacher annotation asset pipeline.
    """
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS hygiene_interception_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            task_id TEXT,
            interception_node TEXT NOT NULL
                CHECK (interception_node IN ('blank', 'short_circuit', 'unreadable')),
            raw_image_path TEXT,
            action TEXT NOT NULL
                CHECK (action IN ('discard', 'manual_review')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS golden_annotation_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            region_id TEXT NOT NULL,
            region_type TEXT NOT NULL
                CHECK (region_type IN ('question_region', 'answer_region')),
            image_width INTEGER NOT NULL CHECK (image_width > 0),
            image_height INTEGER NOT NULL CHECK (image_height > 0),
            bbox_coordinates TEXT NOT NULL,
            perception_ir_snapshot TEXT NOT NULL,
            cognitive_ir_snapshot TEXT NOT NULL,
            teacher_text_feedback TEXT NOT NULL,
            expected_score REAL NOT NULL,
            is_integrated_to_dataset INTEGER NOT NULL DEFAULT 0 CHECK (is_integrated_to_dataset IN (0, 1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_hygiene_trace_id ON hygiene_interception_log(trace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_hygiene_created_at ON hygiene_interception_log(created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_golden_trace_id ON golden_annotation_assets(trace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_golden_task_id ON golden_annotation_assets(task_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_golden_region_id ON golden_annotation_assets(region_id)")
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_golden_trace_region ON golden_annotation_assets(trace_id, region_id)"
    )


async def _ensure_skill_validation_tables(db: aiosqlite.Connection) -> None:
    """
    Phase 43:
    Ensure external skill validation records table exists.
    """
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_validation_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            question_id TEXT,
            checker TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('ok', 'mismatch', 'error')),
            confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
            details_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_skill_validation_task_id ON skill_validation_records(task_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_skill_validation_checker ON skill_validation_records(checker)")


async def _has_legacy_review_columns(db: aiosqlite.Connection) -> bool:
    column_names = await _get_tasks_column_names(db)
    return "human_feedback_json" in column_names or "is_regression_sample" in column_names


async def _migrate_drop_legacy_review_columns(db: aiosqlite.Connection) -> None:
    """
    SQLite-compatible table rebuild executed in the current transaction.
    """
    await db.execute("DROP TABLE IF EXISTS tasks_new")
    await db.execute(
        """
        CREATE TABLE tasks_new (
            task_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            grading_status TEXT
                CHECK (grading_status IN ('SCORED', 'REJECTED_UNREADABLE') OR grading_status IS NULL),
            celery_task_id TEXT,
            rubric_id TEXT,
            error_message TEXT,
            review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'
                CHECK (review_status IN ('NOT_REQUIRED', 'PENDING_REVIEW', 'REVIEWED')),
            fallback_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        INSERT INTO tasks_new
        (task_id, status, grading_status, celery_task_id, rubric_id, error_message, review_status, fallback_reason, created_at, updated_at)
        SELECT task_id, status, grading_status, celery_task_id, rubric_id, error_message, review_status, fallback_reason, created_at, updated_at
        FROM tasks
        """
    )
    await db.execute("DROP TABLE tasks")
    await db.execute("ALTER TABLE tasks_new RENAME TO tasks")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_review_status ON tasks(review_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_grading_status ON tasks(grading_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_rubric_id ON tasks(rubric_id)")


async def migrate_drop_legacy_review_columns(db_path: str) -> None:
    """
    Phase 39 hard-cut:
    Physically drop tasks.human_feedback_json and tasks.is_regression_sample by
    table rebuild (SQLite-compatible migration).
    """
    async def _op(db: aiosqlite.Connection) -> None:
        if await _has_legacy_review_columns(db):
            await _migrate_drop_legacy_review_columns(db)

    await _execute_write_with_retry(db_path, _op)


async def create_task(db_path: str, task_id: str) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_tasks_columns(db, include_celery=False)
        await db.execute(
            "INSERT INTO tasks (task_id, status) VALUES (?, ?)",
            (task_id, "PENDING"),
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
        assignments = [
            "status = ?",
            "error_message = ?",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params: List[Any] = [status, error]
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
        if grading_status_filter:
            sql += " AND grading_status = ?"
            params.append(grading_status_filter)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def create_hygiene_interception_record(
    db_path: str,
    *,
    trace_id: str,
    task_id: Optional[str],
    interception_node: str,
    raw_image_path: Optional[str],
    action: str,
) -> None:
    """
    Insert one hygiene interception record into physically isolated hygiene table.
    """
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_domain_split_tables(db)
        await db.execute(
            """
            INSERT INTO hygiene_interception_log
            (trace_id, task_id, interception_node, raw_image_path, action)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trace_id, task_id, interception_node, raw_image_path, action),
        )

    await _execute_write_with_retry(db_path, _op)


async def list_hygiene_interceptions(
    db_path: str,
    *,
    interception_node_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_domain_split_tables(db)
        db.row_factory = aiosqlite.Row
        sql = (
            "SELECT id, trace_id, task_id, interception_node, raw_image_path, action, created_at "
            "FROM hygiene_interception_log WHERE 1=1"
        )
        params: List[Any] = []
        if interception_node_filter:
            sql += " AND interception_node = ?"
            params.append(interception_node_filter)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_hygiene_interception_by_id(
    db_path: str,
    *,
    record_id: int,
) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_domain_split_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, trace_id, task_id, interception_node, raw_image_path, action, created_at
            FROM hygiene_interception_log
            WHERE id = ?
            """,
            (record_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_hygiene_interception_action(
    db_path: str,
    *,
    record_id: int,
    action: str,
) -> bool:
    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_domain_split_tables(db)
        await db.execute(
            "UPDATE hygiene_interception_log SET action = ? WHERE id = ?",
            (action, record_id),
        )

    await _execute_write_with_retry(db_path, _op)
    row = await get_hygiene_interception_by_id(db_path, record_id=record_id)
    return bool(row)


async def bulk_update_hygiene_interception_action(
    db_path: str,
    *,
    record_ids: Sequence[int],
    action: str,
) -> int:
    ids = [int(x) for x in record_ids]
    if not ids:
        return 0

    placeholders = ",".join("?" for _ in ids)

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_domain_split_tables(db)
        await db.execute(
            f"UPDATE hygiene_interception_log SET action = ? WHERE id IN ({placeholders})",
            (action, *ids),
        )

    await _execute_write_with_retry(db_path, _op)

    async with _open_connection(db_path) as db:
        async with db.execute(
            f"SELECT COUNT(1) FROM hygiene_interception_log WHERE id IN ({placeholders}) AND action = ?",
            (*ids, action),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0


async def create_golden_annotation_asset(
    db_path: str,
    *,
    trace_id: str,
    task_id: str,
    region_id: str,
    region_type: str,
    image_width: int,
    image_height: int,
    bbox_coordinates: Any,
    perception_ir_snapshot: Any,
    cognitive_ir_snapshot: Any,
    teacher_text_feedback: str,
    expected_score: float,
    is_integrated_to_dataset: bool = False,
) -> None:
    """
    Insert one teacher feedback golden asset record.
    """
    bbox_str = _to_json_string(bbox_coordinates)
    perception_snapshot_str = _to_json_string(perception_ir_snapshot)
    cognitive_snapshot_str = _to_json_string(cognitive_ir_snapshot)

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_domain_split_tables(db)
        await db.execute(
            """
            INSERT INTO golden_annotation_assets
            (
                trace_id, task_id, region_id, region_type, image_width, image_height, bbox_coordinates,
                perception_ir_snapshot, cognitive_ir_snapshot, teacher_text_feedback, expected_score,
                is_integrated_to_dataset, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(trace_id, region_id)
            DO UPDATE SET
                task_id=excluded.task_id,
                region_type=excluded.region_type,
                image_width=excluded.image_width,
                image_height=excluded.image_height,
                bbox_coordinates=excluded.bbox_coordinates,
                perception_ir_snapshot=excluded.perception_ir_snapshot,
                cognitive_ir_snapshot=excluded.cognitive_ir_snapshot,
                teacher_text_feedback=excluded.teacher_text_feedback,
                expected_score=excluded.expected_score,
                is_integrated_to_dataset=excluded.is_integrated_to_dataset,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                trace_id,
                task_id,
                region_id,
                region_type,
                image_width,
                image_height,
                bbox_str,
                perception_snapshot_str,
                cognitive_snapshot_str,
                teacher_text_feedback,
                expected_score,
                1 if is_integrated_to_dataset else 0,
            ),
        )

    await _execute_write_with_retry(db_path, _op)


async def list_golden_annotation_assets(
    db_path: str,
    *,
    task_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_domain_split_tables(db)
        db.row_factory = aiosqlite.Row
        sql = (
            "SELECT id, trace_id, task_id, region_id, region_type, image_width, image_height, "
            "bbox_coordinates, teacher_text_feedback, expected_score, is_integrated_to_dataset, "
            "created_at, updated_at "
            "FROM golden_annotation_assets WHERE 1=1"
        )
        params: List[Any] = []
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_rubric(
    db_path: str,
    rubric_id: str,
    question_id: Optional[str],
    rubric_json: Any,
) -> None:
    rubric_str = _to_json_string(rubric_json)

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_rubrics_schema(db)
        await db.execute(
            """
            INSERT INTO rubrics (rubric_id, question_id, rubric_json)
            VALUES (?, ?, ?)
            """,
            (rubric_id, question_id, rubric_str),
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


async def save_grading_result(
    db_path: str,
    task_id: str,
    student_id: str,
    report: Any,
    *,
    question_id: Optional[str] = None,
    perception_output: Any = None,
    cognitive_output: Any = None,
) -> None:
    """
    保存单条批改结果（API/单任务通道）。
    支持可选 perception_output，若提供则与 evaluation_report 组合写入 report_json。
    """
    total_deduction = float(getattr(report, "total_score_deduction"))
    is_pass = bool(getattr(report, "is_fully_correct"))

    if perception_output is None and cognitive_output is None:
        report_json = _to_json_string(report)
    else:
        payload: Dict[str, Any] = {
            "evaluation_report": _to_json_compatible(report),
        }
        if perception_output is not None:
            normalized_perception = _to_json_compatible(perception_output)
            payload["perception_output"] = normalized_perception
            payload["perception_ir_snapshot"] = normalized_perception
        if cognitive_output is not None:
            payload["cognitive_ir_snapshot"] = _to_json_compatible(cognitive_output)
        report_json = _to_json_string(payload)

    async def _op(db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            INSERT INTO grading_results
            (task_id, student_id, question_id, total_deduction, is_pass, report_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, student_id, question_id, total_deduction, is_pass, report_json),
        )

    await _execute_write_with_retry(db_path, _op)


async def insert_grading_results(
    db_path: str,
    records: Sequence[Mapping[str, Any]],
    *,
    task_id: Optional[str] = None,
) -> int:
    """
    批量写入批改结果（批处理通道）。
    records 每项支持两种输入：
    1) 直接给出 report_json（dict/str）
    2) 给出 perception_output + evaluation_report（对象或 dict）
    """
    if not records:
        return 0

    default_task_id = task_id or f"batch-{uuid.uuid4().hex[:12]}"
    rows_to_insert: List[tuple[Any, ...]] = []

    for record in records:
        if "student_id" not in record:
            raise ValueError("Each record must include 'student_id'.")
        if "total_deduction" not in record:
            raise ValueError("Each record must include 'total_deduction'.")
        if "is_pass" not in record:
            raise ValueError("Each record must include 'is_pass'.")

        report_json_raw = record.get("report_json")
        if report_json_raw is None:
            evaluation_report = record.get("evaluation_report")
            if evaluation_report is None:
                raise ValueError("Each record must include either 'report_json' or 'evaluation_report'.")

            report_json_raw = {
                "perception_output": _to_json_compatible(record.get("perception_output")),
                "evaluation_report": _to_json_compatible(evaluation_report),
            }

        report_json = _to_json_string(report_json_raw)

        rows_to_insert.append(
            (
                record.get("task_id") or default_task_id,
                str(record["student_id"]),
                record.get("question_id"),
                float(record["total_deduction"]),
                bool(record["is_pass"]),
                report_json,
            )
        )

    async def _op(db: aiosqlite.Connection) -> None:
        await db.executemany(
            """
            INSERT INTO grading_results
            (task_id, student_id, question_id, total_deduction, is_pass, report_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )

    await _execute_write_with_retry(db_path, _op)
    return len(rows_to_insert)


async def fetch_results(db_path: str, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM grading_results ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
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
