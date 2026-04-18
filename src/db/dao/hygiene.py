import logging
import sqlite3
from typing import Any, Dict, List, Optional, Sequence

import aiosqlite

from src.db.core_utils import _execute_write_with_retry, _open_connection
from src.db.json_utils import _to_json_string
from src.db.dao._migrations import _ensure_domain_split_tables

logger = logging.getLogger(__name__)


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
