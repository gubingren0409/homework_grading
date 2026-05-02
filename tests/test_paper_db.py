import json

import pytest

from src.db.client import get_paper_task, init_db, list_paper_question_results, save_paper_grading_report
from src.schemas.cognitive_ir import EvaluationReport, PaperEvaluationReport


@pytest.mark.asyncio
async def test_save_paper_grading_report_persists_summary_and_question_rows(tmp_path):
    db_path = str(tmp_path / "paper.db")
    await init_db(db_path)

    report = PaperEvaluationReport(
        paper_id="paper-1",
        total_questions=2,
        answered_questions=1,
        total_score_deduction=2.0,
        requires_human_review=True,
        warnings=["question 2 missing"],
        per_question={
            "1": EvaluationReport(
                status="SCORED",
                is_fully_correct=False,
                total_score_deduction=2.0,
                step_evaluations=[],
                overall_feedback="q1",
                system_confidence=1.0,
                requires_human_review=False,
            ),
            "2": EvaluationReport(
                status="REJECTED_UNREADABLE",
                is_fully_correct=False,
                total_score_deduction=0.0,
                step_evaluations=[],
                overall_feedback="q2",
                system_confidence=0.0,
                requires_human_review=True,
            ),
        },
    )

    await save_paper_grading_report(
        db_path,
        "task-1",
        "student-1",
        "bundle-1",
        report,
        question_page_indexes={"1": [0], "2": [1]},
    )

    task_row = await get_paper_task(db_path, "task-1")
    question_rows = await list_paper_question_results(db_path, "task-1")

    assert task_row is not None
    assert task_row["bundle_id"] == "bundle-1"
    assert task_row["paper_id"] == "paper-1"
    assert task_row["requires_human_review"] == 1
    assert len(question_rows) == 2
    assert question_rows[0]["question_id"] == "1"
    assert json.loads(question_rows[0]["page_indexes_json"]) == [0]
    assert question_rows[1]["status"] == "REJECTED_UNREADABLE"
