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
    if "last_heartbeat_at" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN last_heartbeat_at TIMESTAMP")
        column_names.add("last_heartbeat_at")
    if "teacher_id" not in column_names:
        await db.execute("ALTER TABLE tasks ADD COLUMN teacher_id TEXT")
        column_names.add("teacher_id")

    await db.execute("CREATE INDEX IF NOT EXISTS idx_review_status ON tasks(review_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_grading_status ON tasks(grading_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_rubric_id ON tasks(rubric_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_teacher_id ON tasks(teacher_id)")


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
            last_heartbeat_at TIMESTAMP,
            teacher_id TEXT,
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
            fallback_reason, submitted_count, progress, eta_seconds, last_heartbeat_at, teacher_id, created_at, updated_at
        )
        SELECT
            task_id, status, grading_status, celery_task_id, rubric_id, error_message, review_status,
            fallback_reason, COALESCE(submitted_count, 0), COALESCE(progress, 0.0), eta_seconds, last_heartbeat_at, teacher_id, created_at, updated_at
        FROM tasks
        """
    )
    await db.execute("DROP TABLE tasks")
    await db.execute("ALTER TABLE tasks_new RENAME TO tasks")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_review_status ON tasks(review_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_grading_status ON tasks(grading_status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_rubric_id ON tasks(rubric_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_teacher_id ON tasks(teacher_id)")


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


# ---------------------------------------------------------------------------
# DAO re-exports — all public functions have been extracted to domain-specific
# modules under src.db.dao.*. They are re-exported here so that every existing
# ``from src.db.client import X`` continues to work without any caller changes.
# ---------------------------------------------------------------------------

from src.db.dao.tasks import (  # noqa: E402,F401
    create_task,
    update_task_progress,
    set_task_rubric_id,
    set_task_review_status,
    update_task_status,
    update_task_celery_id,
    get_task,
    list_pending_review_tasks,
    list_pending_review_task_rows,
    get_task_status_counts,
    get_task_statuses_by_celery_ids,
    list_processing_tasks,
    list_stale_pending_tasks,
    fail_stale_pending_orphan_tasks,
    get_completion_latencies_seconds,
    get_task_volume_stats,
    insert_skill_validation_records,
    touch_task_heartbeat,
    list_stale_processing_tasks,
    fail_stale_processing_tasks,
)

from src.db.dao.rubrics import (  # noqa: E402,F401
    save_rubric,
    get_rubric,
    list_rubrics,
    get_recent_rubric_by_fingerprint,
    append_rubric_generate_audit,
    list_rubric_generate_audit,
)

from src.db.dao.results import (  # noqa: E402,F401
    save_grading_result,
    insert_grading_results,
    fetch_results,
    fetch_results_by_task,
)

from src.db.dao.review import (  # noqa: E402,F401
    upsert_teacher_review_decision,
    list_teacher_review_decisions,
    get_teacher_review_decision_counts,
)

from src.db.dao.hygiene import (  # noqa: E402,F401
    create_hygiene_interception_record,
    list_hygiene_interceptions,
    get_hygiene_interception_by_id,
    update_hygiene_interception_action,
    bulk_update_hygiene_interception_action,
    create_golden_annotation_asset,
    list_golden_annotation_assets,
    get_annotation_asset_by_id,
)

from src.db.dao.ops import (  # noqa: E402,F401
    get_annotation_dataset_stats,
    get_review_queue_stats,
    get_prompt_cache_level_stats,
    upsert_task_runtime_telemetry,
    get_runtime_telemetry_model_hits,
    get_runtime_telemetry_fallback_stats,
    upsert_prompt_control_state,
    get_prompt_control_state,
    upsert_prompt_ab_config,
    get_prompt_ab_config,
    append_prompt_ops_audit,
    list_prompt_ops_audit,
    get_ops_feature_flags,
    upsert_ops_feature_flags,
    list_ops_release_controls,
    get_ops_release_control,
    upsert_ops_release_control,
    append_ops_fault_drill_report,
    get_ops_fault_drill_report_by_id,
    list_ops_fault_drill_reports,
)


_FORMERLY_HERE = create_task  # noqa: keep — dead anchor for grep audits
