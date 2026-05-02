import asyncio
import io
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

from src.api.dependencies import get_db_path
from src.cognitive.mock_agent import MockCognitiveAgent
from src.core.storage_adapter import storage
from src.db.client import (
    create_task,
    get_paper_task,
    init_db,
    list_paper_question_results,
    save_paper_grading_report,
    save_rubric_bundle,
    update_task_status,
)
from src.main import app
from src.perception.mock_engine import MockPerceptionEngine
from src.schemas.cognitive_ir import EvaluationReport, PaperEvaluationReport
from src.schemas.answer_ir import StudentAnswer, StudentAnswerBundle, StudentAnswerPart
from src.schemas.perception_ir import PerceptionNode
from src.schemas.rubric_ir import RubricBundle, TeacherRubric
from src.skills.interfaces import LayoutParseResult, LayoutRegion


client = TestClient(app)


def _make_test_image_bytes() -> bytes:
    image = Image.new("RGB", (64, 64), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeSkillService:
    def __init__(self, db_path: str) -> None:
        del db_path

    async def try_parse_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        page_index: int = 0,
        target_question_no: str | None = None,
    ) -> LayoutParseResult | None:
        del image_bytes, context_type, target_question_no
        return LayoutParseResult(
            context_type="STUDENT_ANSWER",
            page_index=page_index,
            regions=[
                LayoutRegion(
                    target_id="q1",
                    region_type="title",
                    question_no="1.",
                    bbox={"x_min": 0.10, "y_min": 0.10, "x_max": 0.20, "y_max": 0.15},
                ),
                LayoutRegion(
                    target_id="q1-body",
                    region_type="text",
                    bbox={"x_min": 0.08, "y_min": 0.10, "x_max": 0.80, "y_max": 0.48},
                ),
                LayoutRegion(
                    target_id="q2",
                    region_type="title",
                    question_no="2.",
                    bbox={"x_min": 0.10, "y_min": 0.55, "x_max": 0.20, "y_max": 0.60},
                ),
                LayoutRegion(
                    target_id="q2-body",
                    region_type="text",
                    bbox={"x_min": 0.12, "y_min": 0.55, "x_max": 0.82, "y_max": 0.92},
                ),
            ],
        )


def test_grade_paper_endpoint_persists_report(tmp_path, monkeypatch):
    db_path = str(tmp_path / "paper_api.db")
    asyncio.run(init_db(db_path))
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

    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    monkeypatch.setattr("src.api.routers.grade.create_perception_engine", lambda: MockPerceptionEngine())
    monkeypatch.setattr("src.api.routers.grade.DeepSeekCognitiveEngine", lambda: MockCognitiveAgent())
    monkeypatch.setattr("src.api.routers.grade.SkillService", _FakeSkillService)
    try:
        response = client.post(
            "/api/v1/grade/paper",
            files=[("files", ("student-paper.png", _make_test_image_bytes(), "image/png"))],
            data={"bundle_id": "bundle-1", "student_id": "student-1"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["bundle_id"] == "bundle-1"
        assert payload["paper_id"] == "paper-1"
        assert payload["question_count"] == 2
        assert payload["report_json"]["answered_questions"] == 2

        saved_task = asyncio.run(get_paper_task(db_path, payload["task_id"]))
        question_rows = asyncio.run(list_paper_question_results(db_path, payload["task_id"]))
        assert saved_task is not None
        assert saved_task["student_id"] == "student-1"
        assert len(question_rows) == 2
    finally:
        app.dependency_overrides.clear()


def test_submit_grade_paper_endpoint_enqueues_worker_payload(tmp_path):
    db_path = str(tmp_path / "paper_submit.db")
    asyncio.run(init_db(db_path))
    bundle = RubricBundle(
        paper_id="paper-async",
        rubrics=[TeacherRubric(question_id="1", correct_answer="A")],
        question_tree=[],
    )
    asyncio.run(
        save_rubric_bundle(
            db_path,
            bundle_id="bundle-async",
            paper_id=bundle.paper_id,
            bundle_json=bundle.model_dump(),
        )
    )

    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    try:
        with (
            patch("src.api.routers.grade._check_redis_health", return_value=(True, None)),
            patch("src.api.routers.grade.grade_homework_task.apply_async", return_value=Mock(id="paper-celery-id")) as mocked,
        ):
            response = client.post(
                "/api/v1/grade/paper/submit",
                files=[("files", ("student-paper.png", _make_test_image_bytes(), "image/png"))],
                data={"bundle_id": "bundle-async", "student_id": "student-async"},
            )

        assert response.status_code == 202
        payload = response.json()
        assert payload["mode"] == "paper_submission"
        assert payload["submitted_count"] == 1
        queued_payload = mocked.call_args.kwargs["args"][1]
        assert queued_payload["mode"] == "paper_submission"
        assert queued_payload["bundle_id"] == "bundle-async"
        assert queued_payload["student_id"] == "student-async"
    finally:
        app.dependency_overrides.clear()


def test_submit_grade_paper_endpoint_enqueues_presegmented_question_ids(tmp_path):
    db_path = str(tmp_path / "paper_submit_presegmented.db")
    asyncio.run(init_db(db_path))
    bundle = RubricBundle(
        paper_id="paper-async",
        rubrics=[
            TeacherRubric(question_id="17", correct_answer="A"),
            TeacherRubric(question_id="18", correct_answer="B"),
        ],
        question_tree=[],
    )
    asyncio.run(
        save_rubric_bundle(
            db_path,
            bundle_id="bundle-async",
            paper_id=bundle.paper_id,
            bundle_json=bundle.model_dump(),
        )
    )

    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    try:
        with (
            patch("src.api.routers.grade._check_redis_health", return_value=(True, None)),
            patch("src.api.routers.grade.grade_homework_task.apply_async", return_value=Mock(id="paper-celery-id")) as mocked,
        ):
            response = client.post(
                "/api/v1/grade/paper/submit",
                files=[
                    ("files", ("q17.png", _make_test_image_bytes(), "image/png")),
                    ("files", ("q18.png", _make_test_image_bytes(), "image/png")),
                ],
                data={
                    "bundle_id": "bundle-async",
                    "student_id": "student-async",
                    "presegmented": "true",
                },
            )

        assert response.status_code == 202
        queued_payload = mocked.call_args.kwargs["args"][1]
        assert queued_payload["presegmented_question_ids"] == ["17", "18"]
    finally:
        app.dependency_overrides.clear()


def test_submit_grade_paper_endpoint_allows_presegmented_question_subset(tmp_path):
    db_path = str(tmp_path / "paper_submit_presegmented_subset.db")
    asyncio.run(init_db(db_path))
    bundle = RubricBundle(
        paper_id="paper-async",
        rubrics=[
            TeacherRubric(question_id="一/2", correct_answer="A"),
            TeacherRubric(question_id="一/5", correct_answer="B"),
            TeacherRubric(question_id="四/18", correct_answer="C"),
            TeacherRubric(question_id="四/19", correct_answer="D"),
        ],
        question_tree=[],
    )
    asyncio.run(
        save_rubric_bundle(
            db_path,
            bundle_id="bundle-async",
            paper_id=bundle.paper_id,
            bundle_json=bundle.model_dump(),
        )
    )

    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    try:
        with (
            patch("src.api.routers.grade._check_redis_health", return_value=(True, None)),
            patch("src.api.routers.grade.grade_homework_task.apply_async", return_value=Mock(id="paper-celery-id")) as mocked,
        ):
            response = client.post(
                "/api/v1/grade/paper/submit",
                files=[
                    ("files", ("q2.png", _make_test_image_bytes(), "image/png")),
                    ("files", ("q5.png", _make_test_image_bytes(), "image/png")),
                    ("files", ("q18.png", _make_test_image_bytes(), "image/png")),
                ],
                data={
                    "bundle_id": "bundle-async",
                    "student_id": "student-async",
                    "presegmented": "true",
                    "question_ids": "2,5,18",
                },
            )

        assert response.status_code == 202
        queued_payload = mocked.call_args.kwargs["args"][1]
        assert queued_payload["presegmented_question_ids"] == ["一/2", "一/5", "四/18"]
        assert [item["question_id"] for item in queued_payload["rubric_bundle_json"]["rubrics"]] == [
            "一/2",
            "一/5",
            "四/18",
        ]
    finally:
        app.dependency_overrides.clear()


def test_submit_grade_paper_endpoint_rejects_subset_file_mismatch(tmp_path):
    db_path = str(tmp_path / "paper_submit_presegmented_subset_mismatch.db")
    asyncio.run(init_db(db_path))
    bundle = RubricBundle(
        paper_id="paper-async",
        rubrics=[
            TeacherRubric(question_id="一/2", correct_answer="A"),
            TeacherRubric(question_id="一/5", correct_answer="B"),
            TeacherRubric(question_id="四/18", correct_answer="C"),
        ],
        question_tree=[],
    )
    asyncio.run(
        save_rubric_bundle(
            db_path,
            bundle_id="bundle-async",
            paper_id=bundle.paper_id,
            bundle_json=bundle.model_dump(),
        )
    )

    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    try:
        response = client.post(
            "/api/v1/grade/paper/submit",
            files=[
                ("files", ("q2.png", _make_test_image_bytes(), "image/png")),
                ("files", ("q5.png", _make_test_image_bytes(), "image/png")),
            ],
            data={
                "bundle_id": "bundle-async",
                "student_id": "student-async",
                "presegmented": "true",
                "question_ids": "2,5,18",
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"]["error_code"] == "INPUT_REJECTED"
    finally:
        app.dependency_overrides.clear()


def test_grade_status_endpoint_returns_paper_results_when_present(tmp_path):
    db_path = str(tmp_path / "paper_status.db")
    asyncio.run(init_db(db_path))
    asyncio.run(create_task(db_path, "paper-task-1", submitted_count=1))
    report = PaperEvaluationReport(
        paper_id="paper-status",
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
    asyncio.run(save_paper_grading_report(db_path, "paper-task-1", "student-1", "bundle-1", report))
    asyncio.run(update_task_status(db_path, "paper-task-1", "COMPLETED", grading_status="SCORED"))

    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    try:
        response = client.get("/api/v1/grade/paper-task-1")

        assert response.status_code == 200
        payload = response.json()
        assert payload["result_count"] == 2
        assert payload["results"][0]["paper_report"]["paper_id"] == "paper-status"
        assert payload["results"][0]["paper_task"]["report_json"]["paper_id"] == "paper-status"
        assert len(payload["results"][0]["question_results"]) == 2

        report_response = client.get("/api/v1/grade/paper-task-1/report")
        assert report_response.status_code == 200
        report_payload = report_response.json()
        assert len(report_payload["cards"]) == 2
        assert report_payload["cards"][0]["student_id"] == "student-1｜1"

        history_response = client.get("/api/v1/tasks/history?limit=5")
        assert history_response.status_code == 200
        history_item = next(item for item in history_response.json()["items"] if item["task_id"] == "paper-task-1")
        assert history_item["result_count"] == 2
    finally:
        app.dependency_overrides.clear()


def test_paper_reports_endpoint_lists_all_students_for_bundle(tmp_path):
    db_path = str(tmp_path / "paper_reports.db")
    asyncio.run(init_db(db_path))
    report_a = PaperEvaluationReport(
        paper_id="paper-class",
        total_questions=1,
        answered_questions=1,
        total_score_deduction=0.0,
        requires_human_review=False,
        warnings=[],
        per_question={
            "1": EvaluationReport(
                status="SCORED",
                is_fully_correct=True,
                total_score_deduction=0.0,
                step_evaluations=[],
                overall_feedback="ok",
                system_confidence=1.0,
                requires_human_review=False,
            )
        },
    )
    report_b = PaperEvaluationReport(
        paper_id="paper-class",
        total_questions=1,
        answered_questions=1,
        total_score_deduction=1.0,
        requires_human_review=True,
        warnings=["needs review"],
        per_question={
            "1": EvaluationReport(
                status="SCORED",
                is_fully_correct=False,
                total_score_deduction=1.0,
                step_evaluations=[
                    {
                        "reference_element_id": "p0_answer_1_part0_0_raw-step",
                        "is_correct": False,
                        "error_type": "CALCULATION",
                        "correction_suggestion": "check calculation",
                    }
                ],
                overall_feedback="deduct",
                system_confidence=0.8,
                requires_human_review=True,
            )
        },
        student_answer_bundle=StudentAnswerBundle(
            paper_id="paper-class",
            answers=[
                StudentAnswer(
                    question_id="1",
                    answer_text="student answer",
                    ocr_text="student answer with printed context",
                    parts=[
                        StudentAnswerPart(
                            source_question_no="1",
                            text="student answer with printed context",
                            answer_text="student answer",
                            elements=[
                                PerceptionNode(
                                    element_id="raw-step",
                                    content_type="plain_text",
                                    raw_content="10 / 2 = 4",
                                    confidence_score=0.9,
                                )
                            ],
                            global_confidence=0.9,
                            readability_status="CLEAR",
                        )
                    ],
                    global_confidence=0.9,
                )
            ],
        ),
    )
    asyncio.run(create_task(db_path, "paper-task-a", submitted_count=1))
    asyncio.run(save_paper_grading_report(db_path, "paper-task-a", "student-a", "bundle-class", report_a))
    asyncio.run(update_task_status(db_path, "paper-task-a", "COMPLETED", grading_status="SCORED"))
    asyncio.run(create_task(db_path, "paper-task-b", submitted_count=1))
    q1_file_ref = storage.store_file("paper-task-b", _make_test_image_bytes(), "q1.png")
    asyncio.run(
        save_paper_grading_report(
            db_path,
            "paper-task-b",
            "student-b",
            "bundle-class",
            report_b,
            question_input_file_refs={"1": [q1_file_ref]},
            question_input_filenames={"1": ["q1.png"]},
        )
    )
    asyncio.run(update_task_status(db_path, "paper-task-b", "COMPLETED", grading_status="SCORED"))

    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    try:
        response = client.get("/api/v1/grade/paper/reports?bundle_id=bundle-class")
        assert response.status_code == 200
        payload = response.json()
        assert payload["student_count"] == 2
        assert payload["completed_count"] == 2
        assert payload["review_count"] == 1
        assert payload["question_ids"] == ["1"]
        assert [item["student_id"] for item in payload["students"]] == ["student-a", "student-b"]
        assert len(payload["students"][0]["question_results"]) == 1
        step = payload["students"][1]["paper_report"]["per_question"]["1"]["step_evaluations"][0]
        assert step["evidence_snippet"] == "10 / 2 = 4"
        image_item = payload["students"][1]["paper_report"]["input_images_by_question"]["1"][0]
        assert image_item["name"] == "q1.png"
        image_response = client.get(image_item["url"])
        assert image_response.status_code == 200
        assert image_response.headers["content-type"] == "image/png"
    finally:
        app.dependency_overrides.clear()
