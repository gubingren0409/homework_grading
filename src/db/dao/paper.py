from typing import Any, Dict, List, Mapping, Optional, Sequence

import aiosqlite

from src.db.core_utils import _execute_write_with_retry, _open_connection
from src.db.dao._migrations import _ensure_paper_grading_tables
from src.db.json_utils import _to_json_compatible, _to_json_string
from src.schemas.cognitive_ir import PaperEvaluationReport


async def save_paper_grading_report(
    db_path: str,
    task_id: str,
    student_id: str,
    bundle_id: str,
    report: PaperEvaluationReport,
    *,
    question_page_indexes: Optional[Mapping[str, Sequence[int]]] = None,
    question_input_file_refs: Optional[Mapping[str, Sequence[str]]] = None,
    question_input_filenames: Optional[Mapping[str, Sequence[str]]] = None,
) -> None:
    report_payload = _to_json_compatible(report)
    if question_input_file_refs:
        report_payload["input_file_refs_by_question"] = {
            str(question_id): [str(file_ref) for file_ref in file_refs]
            for question_id, file_refs in question_input_file_refs.items()
        }
    if question_input_filenames:
        report_payload["input_filenames_by_question"] = {
            str(question_id): [str(filename) for filename in filenames]
            for question_id, filenames in question_input_filenames.items()
        }
    report_json = _to_json_string(report_payload)
    question_page_indexes = question_page_indexes or {}
    question_rows = [
        (
            task_id,
            student_id,
            question_id,
            _to_json_string(list(question_page_indexes.get(question_id, []))),
            float(question_report.total_score_deduction),
            question_report.status,
            1 if question_report.requires_human_review else 0,
            _to_json_string(_to_json_compatible(question_report)),
        )
        for question_id, question_report in report.per_question.items()
    ]

    async def _op(db: aiosqlite.Connection) -> None:
        await _ensure_paper_grading_tables(db)
        await db.execute(
            """
            INSERT OR REPLACE INTO paper_tasks
            (task_id, student_id, bundle_id, paper_id, total_questions, answered_questions,
             total_score_deduction, requires_human_review, report_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                task_id,
                student_id,
                bundle_id,
                report.paper_id,
                report.total_questions,
                report.answered_questions,
                report.total_score_deduction,
                1 if report.requires_human_review else 0,
                report_json,
            ),
        )
        await db.execute("DELETE FROM paper_question_results WHERE task_id = ?", (task_id,))
        await db.executemany(
            """
            INSERT INTO paper_question_results
            (task_id, student_id, question_id, page_indexes_json, total_deduction,
             status, requires_human_review, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            question_rows,
        )

    await _execute_write_with_retry(db_path, _op)


async def get_paper_task(db_path: str, task_id: str) -> Optional[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM paper_tasks WHERE task_id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_paper_question_results(db_path: str, task_id: str) -> List[Dict[str, Any]]:
    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM paper_question_results
            WHERE task_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (task_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
