import asyncio
import io
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

from src.api.dependencies import get_db_path
from src.cognitive.mock_agent import MockCognitiveAgent
from src.main import app
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput
from src.skills.interfaces import LayoutParseResult, LayoutRegion
from src.worker.main import grade_homework_task
from src.db.client import init_db


client = TestClient(app)


def _make_test_image_bytes() -> bytes:
    image = Image.new("RGB", (96, 96), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _E2EPerceptionEngine:
    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        del image_bytes
        outputs = await self.process_images([b"single"])
        return outputs[0]

    async def process_images(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str = "student_homework",
    ) -> list[PerceptionOutput]:
        if context_type == "REFERENCE":
            return [
                PerceptionOutput(
                    readability_status="CLEAR",
                    elements=[
                        PerceptionNode(
                            element_id="q1",
                            content_type="plain_text",
                            raw_content="1．参考答案A",
                            confidence_score=1.0,
                            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.18),
                        ),
                        PerceptionNode(
                            element_id="q2",
                            content_type="plain_text",
                            raw_content="2．参考答案B",
                            confidence_score=1.0,
                            bbox=BoundingBox(x_min=0.1, y_min=0.3, x_max=0.5, y_max=0.38),
                        ),
                    ],
                    global_confidence=1.0,
                )
                for _ in image_bytes_list
            ]
        if context_type == "student_paper_pages":
            return [
                PerceptionOutput(
                    readability_status="CLEAR",
                    elements=[
                        PerceptionNode(
                            element_id="q1",
                            content_type="plain_text",
                            raw_content="1．",
                            confidence_score=1.0,
                            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.2, y_max=0.15),
                        ),
                        PerceptionNode(
                            element_id="q2",
                            content_type="plain_text",
                            raw_content="2．",
                            confidence_score=1.0,
                            bbox=BoundingBox(x_min=0.1, y_min=0.55, x_max=0.2, y_max=0.60),
                        ),
                    ],
                    global_confidence=1.0,
                )
                for _ in image_bytes_list
            ]
        if context_type == "student_answer_regions":
            return [
                PerceptionOutput(
                    readability_status="CLEAR",
                    elements=[
                        PerceptionNode(
                            element_id=f"answer-{index}",
                            content_type="plain_text",
                            raw_content=f"<student>student answer {index}</student>",
                            confidence_score=1.0,
                            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.8),
                        )
                    ],
                    global_confidence=1.0,
                )
                for index, _ in enumerate(image_bytes_list, start=1)
            ]
        return [
            PerceptionOutput(
                readability_status="CLEAR",
                elements=[],
                global_confidence=1.0,
            )
            for _ in image_bytes_list
        ]


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


async def _noop_publish(*args, **kwargs):
    del args, kwargs


def test_paper_reference_submit_worker_report_e2e(tmp_path):
    db_path = str(tmp_path / "paper_e2e.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()

    try:
        with (
            patch("src.api.routers.rubric.create_perception_engine", lambda: _E2EPerceptionEngine()),
            patch("src.api.routers.rubric.DeepSeekCognitiveEngine", lambda: MockCognitiveAgent()),
            patch("src.api.routers.grade._check_redis_health", return_value=(True, None)),
            patch("src.api.routers.grade.grade_homework_task.apply_async", return_value=Mock(id="paper-e2e-celery")) as mocked,
            patch("src.worker.main.create_perception_engine", lambda: _E2EPerceptionEngine()),
            patch("src.worker.main.DeepSeekCognitiveEngine", lambda: MockCognitiveAgent()),
            patch("src.worker.main.SkillService", _FakeSkillService),
            patch("src.worker.main._publish_status", _noop_publish),
        ):
            rubric_response = client.post(
                "/api/v1/rubric/bundle/generate",
                files=[("files", ("reference.png", _make_test_image_bytes(), "image/png"))],
            )

            assert rubric_response.status_code == 201
            rubric_payload = rubric_response.json()
            assert rubric_payload["question_count"] == 2
            assert [item["question_id"] for item in rubric_payload["bundle_json"]["rubrics"]] == ["1", "2"]

            submit_response = client.post(
                "/api/v1/grade/paper/submit",
                files=[("files", ("student-paper.png", _make_test_image_bytes(), "image/png"))],
                data={"bundle_id": rubric_payload["bundle_id"], "student_id": "student-e2e"},
            )

            assert submit_response.status_code == 202
            submit_payload = submit_response.json()
            queued_payload = mocked.call_args.kwargs["args"][1]
            assert queued_payload["mode"] == "paper_submission"

            worker_result = grade_homework_task(
                submit_payload["task_id"],
                queued_payload,
                db_path,
            )

            assert worker_result["status"] == "success"

            reports_response = client.get(
                f"/api/v1/grade/paper/reports?bundle_id={rubric_payload['bundle_id']}"
            )
            assert reports_response.status_code == 200
            reports_payload = reports_response.json()
            assert reports_payload["student_count"] == 1
            assert reports_payload["completed_count"] == 1
            assert reports_payload["question_ids"] == ["1", "2"]
            assert reports_payload["question_stats"] == [
                {
                    "question_id": "1",
                    "student_count": 1,
                    "answered_count": 1,
                    "review_count": 0,
                    "fully_correct_count": 0,
                    "average_deduction": 2.0,
                },
                {
                    "question_id": "2",
                    "student_count": 1,
                    "answered_count": 1,
                    "review_count": 0,
                    "fully_correct_count": 0,
                    "average_deduction": 2.0,
                },
            ]

            paper_report = reports_payload["students"][0]["paper_report"]
            assert paper_report["answered_questions"] == 2
            assert sorted(paper_report["per_question"].keys()) == ["1", "2"]
            assert len(paper_report["input_images_by_question"]["1"]) == 1

            crop_response = client.get(
                f"/api/v1/grade/paper/inputs?task_id={submit_payload['task_id']}&question_id=1&index=0&asset_kind=crop"
            )
            assert crop_response.status_code == 200
            assert crop_response.headers["content-type"] == "image/png"
    finally:
        app.dependency_overrides.clear()
