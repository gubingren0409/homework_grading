import asyncio
import json
import os
import random
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional, Sequence

import aiosqlite


# 写操作锁重试参数：指数退避 + 轻微抖动，缓解高并发 "database is locked"。
_WRITE_LOCK_MAX_RETRIES: int = 6
_WRITE_BACKOFF_BASE_SECONDS: float = 0.05
_WRITE_BACKOFF_MAX_SECONDS: float = 1.0
_SQLITE_BUSY_TIMEOUT_MS: int = 5000


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

    await _execute_write_with_retry(db_path, _op)


async def create_task(db_path: str, task_id: str) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await db.execute(
            "INSERT INTO tasks (task_id, status) VALUES (?, ?)",
            (task_id, "PENDING"),
        )

    await _execute_write_with_retry(db_path, _op)


async def update_task_status(
    db_path: str,
    task_id: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    async def _op(db: aiosqlite.Connection) -> None:
        await db.execute(
            "UPDATE tasks SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (status, error, task_id),
        )

    await _execute_write_with_retry(db_path, _op)


async def get_task(db_path: str, task_id: str) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def save_grading_result(
    db_path: str,
    task_id: str,
    student_id: str,
    report: Any,
    *,
    question_id: Optional[str] = None,
    perception_output: Any = None,
) -> None:
    """
    保存单条批改结果（API/单任务通道）。
    支持可选 perception_output，若提供则与 evaluation_report 组合写入 report_json。
    """
    total_deduction = float(getattr(report, "total_score_deduction"))
    is_pass = bool(getattr(report, "is_fully_correct"))

    if perception_output is None:
        report_json = _to_json_string(report)
    else:
        report_json = _to_json_string(
            {
                "perception_output": _to_json_compatible(perception_output),
                "evaluation_report": _to_json_compatible(report),
            }
        )

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
