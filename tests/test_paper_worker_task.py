import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.core.storage_adapter import storage
from src.db.client import create_task, get_paper_task, init_db, list_paper_question_results, save_rubric_bundle
from src.schemas.cognitive_ir import EvaluationReport, PaperEvaluationReport
from src.schemas.rubric_ir import RubricBundle, TeacherRubric
from src.worker.main import grade_homework_task


class _FakePaperWorkflow:
    async def run_pipeline(self, files_data, rubric_bundle):
        assert len(files_data) == 1
        assert rubric_bundle.paper_id == "paper-1"
        return self._report()

    async def run_pipeline_with_presegmented_images(
        self,
        image_bytes_list,
        rubric_bundle,
        *,
        presegmented_question_ids=None,
    ):
        assert len(image_bytes_list) == 2
        assert presegmented_question_ids == ["1", "2"]
        assert rubric_bundle.paper_id == "paper-1"
        return self._report()

    def _report(self):
        return PaperEvaluationReport(
            paper_id="paper-1",
            total_questions=2,
            answered_questions=2,
            total_score_deduction=3.0,
            requires_human_review=False,
            warnings=[],
            per_question={
                "1": EvaluationReport(
                    status="SCORED",
                    is_fully_correct=False,
                    total_score_deduction=1.0,
                    step_evaluations=[],
                    overall_feedback="q1",
                    system_confidence=1.0,
                    requires_human_review=False,
                ),
                "2": EvaluationReport(
                    status="SCORED",
                    is_fully_correct=False,
                    total_score_deduction=2.0,
                    step_evaluations=[],
                    overall_feedback="q2",
                    system_confidence=1.0,
                    requires_human_review=False,
                ),
            },
        )


async def _noop_publish(*args, **kwargs):
    del args, kwargs


def test_worker_paper_submission_mode_persists_paper_results(tmp_path):
    db_path = str(tmp_path / "paper_worker.db")
    asyncio.run(init_db(db_path))
    asyncio.run(create_task(db_path, "paper-task-1"))
    bundle = RubricBundle(
        paper_id="paper-1",
        rubrics=[
            TeacherRubric(question_id="1", correct_answer="A"),
            TeacherRubric(question_id="2", correct_answer="B"),
        ],
        question_tree=[],
    )
    asyncio.run(
        save_rubric_bundle(
            db_path,
            bundle_id="bundle-1",
            paper_id=bundle.paper_id,
            bundle_json=bundle.model_dump(),
        )
    )

    image_bytes = b"fake-paper-bytes"
    file_ref = storage.store_file("paper-task-1", image_bytes, "paper.png")
    payload = storage.prepare_payload([file_ref])
    payload["mode"] = "paper_submission"
    payload["bundle_id"] = "bundle-1"
    payload["student_id"] = "student-1"

    with (
        patch("src.worker.main._build_workflow", side_effect=AssertionError("paper mode should not build generic workflow")),
        patch("src.worker.main._build_paper_workflow", return_value=_FakePaperWorkflow()),
        patch("src.worker.main._publish_status", _noop_publish),
    ):
        result = grade_homework_task("paper-task-1", payload, db_path)

    assert result["status"] == "success"
    paper_task = asyncio.run(get_paper_task(db_path, "paper-task-1"))
    question_rows = asyncio.run(list_paper_question_results(db_path, "paper-task-1"))
    assert paper_task is not None
    assert paper_task["bundle_id"] == "bundle-1"
    assert paper_task["student_id"] == "student-1"
    assert len(question_rows) == 2


def test_worker_paper_submission_presegmented_mode_uses_question_order(tmp_path):
    db_path = str(tmp_path / "paper_worker_presegmented.db")
    asyncio.run(init_db(db_path))
    asyncio.run(create_task(db_path, "paper-task-presegmented"))
    bundle = RubricBundle(
        paper_id="paper-1",
        rubrics=[
            TeacherRubric(question_id="1", correct_answer="A"),
            TeacherRubric(question_id="2", correct_answer="B"),
        ],
        question_tree=[],
    )
    asyncio.run(
        save_rubric_bundle(
            db_path,
            bundle_id="bundle-1",
            paper_id=bundle.paper_id,
            bundle_json=bundle.model_dump(),
        )
    )

    file_refs = [
        storage.store_file("paper-task-presegmented", b"fake-image-1", "q1.png"),
        storage.store_file("paper-task-presegmented", b"fake-image-2", "q2.png"),
    ]
    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "paper_submission"
    payload["bundle_id"] = "bundle-1"
    payload["student_id"] = "student-1"
    payload["presegmented_question_ids"] = ["1", "2"]

    process_mock = AsyncMock(return_value=[b"img1", b"img2"])
    with (
        patch("src.worker.main._build_workflow", side_effect=AssertionError("paper mode should not build generic workflow")),
        patch("src.worker.main._build_paper_workflow", return_value=_FakePaperWorkflow()),
        patch("src.worker.main._publish_status", _noop_publish),
        patch("src.worker.main.process_multiple_files", new=process_mock),
    ):
        result = grade_homework_task("paper-task-presegmented", payload, db_path)

    assert result["status"] == "success"
    process_mock.assert_not_awaited()
    question_rows = asyncio.run(list_paper_question_results(db_path, "paper-task-presegmented"))
    assert len(question_rows) == 2
