import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import aiosqlite
from src.db.core_utils import _execute_write_with_retry, _open_connection
from src.db.json_utils import _to_json_compatible, _to_json_string


logger = logging.getLogger(__name__)

_STATUS_WEIGHTS: Dict[str, int] = {
    "PENDING": 0,
    "PROCESSING": 1,
    "COMPLETED": 2,
    "FAILED": 3,
}


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
        await _ensure_rubric_audit_schema(db)
        await _ensure_domain_split_tables(db)
        await _ensure_skill_validation_tables(db)
        await _ensure_runtime_telemetry_table(db)
        await _ensure_prompt_control_tables(db)
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
    if "submitted_count" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN submitted_count INTEGER NOT NULL DEFAULT 0")
        column_names.add("submitted_count")
    if "progress" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN progress REAL NOT NULL DEFAULT 0")
        column_names.add("progress")
    if "eta_seconds" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN eta_seconds INTEGER")
        column_names.add("eta_seconds")

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
            source_fingerprint TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    async with db.execute("PRAGMA table_info(rubrics)") as cursor:
        columns = await cursor.fetchall()
    column_names = {str(col[1]) for col in columns}
    if "source_fingerprint" not in column_names:
        await db.execute("ALTER TABLE rubrics ADD COLUMN source_fingerprint TEXT")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rubrics_created_at ON rubrics(created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rubrics_source_fingerprint ON rubrics(source_fingerprint)")


async def _ensure_rubric_audit_schema(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS rubric_generate_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            rubric_id TEXT,
            source_fingerprint TEXT NOT NULL,
            reused_from_cache INTEGER NOT NULL CHECK (reused_from_cache IN (0, 1)),
            force_regenerate INTEGER NOT NULL CHECK (force_regenerate IN (0, 1)),
            source_file_count INTEGER NOT NULL CHECK (source_file_count >= 1),
            client_ip TEXT,
            user_agent TEXT,
            referer TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_rubric_generate_audit_created_at ON rubric_generate_audit(created_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_rubric_generate_audit_fingerprint ON rubric_generate_audit(source_fingerprint)"
    )


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
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_review_decisions (
            task_id TEXT NOT NULL,
            sample_id TEXT NOT NULL,
            student_id TEXT,
            decision TEXT NOT NULL
                CHECK (decision IN ('CONFIRM_MACHINE', 'ADJUST_SCORE', 'MARK_UNREADABLE', 'ESCALATE')),
            final_score REAL,
            teacher_comment TEXT NOT NULL,
            include_in_dataset INTEGER NOT NULL DEFAULT 0 CHECK (include_in_dataset IN (0, 1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, sample_id),
            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_hygiene_trace_id ON hygiene_interception_log(trace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_hygiene_created_at ON hygiene_interception_log(created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_golden_trace_id ON golden_annotation_assets(trace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_golden_task_id ON golden_annotation_assets(task_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_golden_region_id ON golden_annotation_assets(region_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_review_decisions_task_id ON teacher_review_decisions(task_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_review_decisions_student_id ON teacher_review_decisions(student_id)")
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


async def _ensure_runtime_telemetry_table(db: aiosqlite.Connection) -> None:
    """
    Phase P0:
    Ensure runtime telemetry table exists for durable model/prompt observability.
    """
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS task_runtime_telemetry (
            task_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            requested_model TEXT NOT NULL,
            model_used TEXT NOT NULL,
            route_reason TEXT NOT NULL,
            fallback_used INTEGER NOT NULL CHECK (fallback_used IN (0, 1)),
            fallback_reason TEXT,
            prompt_key TEXT NOT NULL,
            prompt_asset_version TEXT NOT NULL,
            prompt_variant_id TEXT NOT NULL,
            prompt_cache_level TEXT NOT NULL
                CHECK (prompt_cache_level IN ('L1', 'L2', 'SOURCE', 'LKG')),
            prompt_token_estimate INTEGER NOT NULL CHECK (prompt_token_estimate >= 0),
            succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_model_used ON task_runtime_telemetry(model_used)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_created_at ON task_runtime_telemetry(created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_cache_level ON task_runtime_telemetry(prompt_cache_level)")


async def _ensure_prompt_control_tables(db: aiosqlite.Connection) -> None:
    """
    Phase P1:
    Ensure prompt hot-reload + AB control plane tables exist.
    """
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_control_state (
            prompt_key TEXT PRIMARY KEY,
            forced_variant_id TEXT,
            lkg_mode INTEGER NOT NULL DEFAULT 0 CHECK (lkg_mode IN (0, 1)),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_ab_configs (
            prompt_key TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            rollout_percentage INTEGER NOT NULL CHECK (rollout_percentage >= 0 AND rollout_percentage <= 100),
            variant_weights_json TEXT NOT NULL,
            segment_prefixes_json TEXT NOT NULL,
            sticky_salt TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_ops_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            operator_id TEXT,
            action TEXT NOT NULL,
            prompt_key TEXT,
            payload_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_ops_action ON prompt_ops_audit_log(action)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_ops_created_at ON prompt_ops_audit_log(created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_ops_prompt_key ON prompt_ops_audit_log(prompt_key)")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_feature_flags (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            deployment_environment TEXT NOT NULL DEFAULT 'dev',
            provider_switch_enabled INTEGER NOT NULL DEFAULT 1 CHECK (provider_switch_enabled IN (0, 1)),
            prompt_control_enabled INTEGER NOT NULL DEFAULT 1 CHECK (prompt_control_enabled IN (0, 1)),
            router_control_enabled INTEGER NOT NULL DEFAULT 1 CHECK (router_control_enabled IN (0, 1)),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        INSERT INTO ops_feature_flags (
            id, deployment_environment, provider_switch_enabled, prompt_control_enabled, router_control_enabled
        )
        VALUES (1, 'dev', 1, 1, 1)
        ON CONFLICT(id) DO NOTHING
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_release_controls (
            layer TEXT PRIMARY KEY
                CHECK (layer IN ('api', 'prompt', 'router')),
            strategy TEXT NOT NULL DEFAULT 'stable'
                CHECK (strategy IN ('stable', 'canary', 'rollback')),
            rollout_percentage INTEGER NOT NULL DEFAULT 100
                CHECK (rollout_percentage >= 0 AND rollout_percentage <= 100),
            target_version TEXT,
            config_json TEXT NOT NULL DEFAULT '{}',
            rollback_config_json TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for layer in ("api", "prompt", "router"):
        await db.execute(
            """
            INSERT INTO ops_release_controls (
                layer, strategy, rollout_percentage, target_version, config_json, rollback_config_json
            )
            VALUES (?, 'stable', 100, NULL, '{}', '{}')
            ON CONFLICT(layer) DO NOTHING
            """,
            (layer,),
        )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_fault_drill_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drill_type TEXT NOT NULL
                CHECK (drill_type IN ('redis_unavailable', 'model_failure', 'sse_disconnect', 'db_pressure')),
            status TEXT NOT NULL
                CHECK (status IN ('passed', 'failed')),
            details_json TEXT NOT NULL,
            operator_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ops_fault_drill_type ON ops_fault_drill_reports(drill_type)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ops_fault_drill_created_at ON ops_fault_drill_reports(created_at)")


async def _has_legacy_review_columns(db: aiosqlite.Connection) -> bool:
    column_names = await _get_tasks_column_names(db)
    return "human_feedback_json" in column_names or "is_regression_sample" in column_names


async def _migrate_drop_legacy_review_columns(db: aiosqlite.Connection) -> None:
    """
    SQLite-compatible table rebuild executed in the current transaction.
    """
    await _ensure_tasks_columns(db, include_celery=True)
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
            submitted_count INTEGER NOT NULL DEFAULT 0
                CHECK (submitted_count >= 0),
            progress REAL NOT NULL DEFAULT 0
                CHECK (progress >= 0.0 AND progress <= 1.0),
            eta_seconds INTEGER
                CHECK (eta_seconds IS NULL OR eta_seconds >= 0),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        INSERT INTO tasks_new
        (
            task_id, status, grading_status, celery_task_id, rubric_id, error_message, review_status,
            fallback_reason, submitted_count, progress, eta_seconds, created_at, updated_at
        )
        SELECT
            task_id, status, grading_status, celery_task_id, rubric_id, error_message, review_status,
            fallback_reason, COALESCE(submitted_count, 0), COALESCE(progress, 0.0), eta_seconds, created_at, updated_at
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
    region_id: Optional[str] = None,
    region_type: Optional[str] = None,
    integrated_only: Optional[bool] = None,
    order_by: str = "created_at",
    order_direction: str = "desc",
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
        if region_id:
            sql += " AND region_id = ?"
            params.append(region_id)
        if region_type:
            sql += " AND region_type = ?"
            params.append(region_type)
        if integrated_only is not None:
            sql += " AND is_integrated_to_dataset = ?"
            params.append(1 if integrated_only else 0)

        order_candidates = {
            "created_at": "created_at",
            "updated_at": "updated_at",
            "id": "id",
        }
        normalized_order = order_candidates.get(str(order_by).strip().lower(), "created_at")
        normalized_direction = "ASC" if str(order_direction).strip().lower() == "asc" else "DESC"
        sql += f" ORDER BY {normalized_order} {normalized_direction}, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_annotation_asset_by_id(
    db_path: str,
    *,
    asset_id: int,
) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        await _ensure_domain_split_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, trace_id, task_id, region_id, region_type, image_width, image_height,
                   bbox_coordinates, perception_ir_snapshot, cognitive_ir_snapshot,
                   teacher_text_feedback, expected_score, is_integrated_to_dataset,
                   created_at, updated_at
            FROM golden_annotation_assets
            WHERE id = ?
            """,
            (asset_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


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


async def save_grading_result(
    db_path: str,
    task_id: str,
    student_id: str,
    report: Any,
    *,
    question_id: Optional[str] = None,
    perception_output: Any = None,
    cognitive_output: Any = None,
    report_payload_extras: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    保存单条批改结果（API/单任务通道）。
    支持可选 perception_output，若提供则与 evaluation_report 组合写入 report_json。
    """
    total_deduction = float(getattr(report, "total_score_deduction"))
    is_pass = bool(getattr(report, "is_fully_correct"))

    if perception_output is None and cognitive_output is None and not report_payload_extras:
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
        if report_payload_extras:
            for key, value in report_payload_extras.items():
                payload[str(key)] = _to_json_compatible(value)
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


async def fetch_results_by_task(db_path: str, task_id: str) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM grading_results
            WHERE task_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (task_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


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
