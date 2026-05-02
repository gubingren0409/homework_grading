import io

import pytest
from PIL import Image

from src.orchestration.rubric_bundle_workflow import RubricBundleWorkflow
from src.perception.base import BasePerceptionEngine
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput
from src.schemas.rubric_ir import GradingPoint, TeacherRubric


def _make_test_image_bytes() -> bytes:
    image = Image.new("RGB", (128, 128), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _HandwrittenReferencePerception(BasePerceptionEngine):
    def __init__(self) -> None:
        self._call_count = 0
        self.contexts: list[str] = []

    async def process_images(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str = "student_homework",
    ) -> list[PerceptionOutput]:
        self.contexts.append(context_type)
        return [await self.process_image(image_bytes) for image_bytes in image_bytes_list]

    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        del image_bytes
        self._call_count += 1
        if self._call_count > 1:
            return PerceptionOutput(
                readability_status="CLEAR",
                elements=[
                    PerceptionNode(
                        element_id=f"answer-{self._call_count}",
                        content_type="plain_text",
                        raw_content=f"老师手写答案片段 {self._call_count - 1}",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.2),
                    ),
                ],
                global_confidence=1.0,
            )
        return PerceptionOutput(
            readability_status="CLEAR",
            elements=[
                PerceptionNode(
                    element_id="q12",
                    content_type="plain_text",
                    raw_content="12．实验题",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.15),
                ),
                PerceptionNode(
                    element_id="sub1",
                    content_type="plain_text",
                    raw_content="(1) 第一问",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.2, x_max=0.8, y_max=0.25),
                ),
                PerceptionNode(
                    element_id="sub2",
                    content_type="plain_text",
                    raw_content="(2) 第二问",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.4, x_max=0.8, y_max=0.45),
                ),
                PerceptionNode(
                    element_id="sub3",
                    content_type="plain_text",
                    raw_content="(3) 第三问，这里集中写了(1)(2)(3)的答案",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.6, x_max=0.8, y_max=0.65),
                ),
            ],
            global_confidence=1.0,
        )


class _PrintedSubquestionReferencePerception(BasePerceptionEngine):
    def __init__(self) -> None:
        self.contexts: list[str] = []

    async def process_images(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str = "student_homework",
    ) -> list[PerceptionOutput]:
        self.contexts.append(context_type)
        return [await self.process_image(image_bytes) for image_bytes in image_bytes_list]

    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        del image_bytes
        return PerceptionOutput(
            readability_status="CLEAR",
            elements=[
                PerceptionNode(
                    element_id="q12",
                    content_type="plain_text",
                    raw_content="12．实验题",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.15),
                ),
                PerceptionNode(
                    element_id="sub1",
                    content_type="plain_text",
                    raw_content="(1) A",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.2, x_max=0.8, y_max=0.25),
                ),
                PerceptionNode(
                    element_id="sub2",
                    content_type="plain_text",
                    raw_content="(2) B",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.4, x_max=0.8, y_max=0.45),
                ),
            ],
            global_confidence=1.0,
        )


class _RubricGeneratingCognitiveAgent:
    def __init__(self, *, score: float = 2.0) -> None:
        self.calls: list[PerceptionOutput] = []
        self.score = score

    async def evaluate_logic(self, perception_data, rubric=None) -> EvaluationReport:
        raise AssertionError("not used")

    async def generate_rubric(self, perception_data: PerceptionOutput) -> TeacherRubric:
        self.calls.append(perception_data)
        return TeacherRubric(
            question_id="generated",
            correct_answer="generated",
            grading_points=[
                GradingPoint(
                    point_id="generated-p1",
                    description="generated grading point",
                    score=self.score,
                )
            ],
        )


class _ScopedRubricGeneratingCognitiveAgent:
    def __init__(self) -> None:
        self.calls: list[PerceptionOutput] = []

    async def evaluate_logic(self, perception_data, rubric=None) -> EvaluationReport:
        raise AssertionError("not used")

    async def generate_rubric(self, perception_data: PerceptionOutput) -> TeacherRubric:
        self.calls.append(perception_data)
        return TeacherRubric(
            question_id="generated",
            correct_answer="generated",
            grading_points=[
                GradingPoint(
                    point_id="generated-1a",
                    description="generated scope one",
                    score=1.0,
                    scope="(1)",
                ),
                GradingPoint(
                    point_id="generated-2a",
                    description="generated scope two first",
                    score=1.0,
                    scope="(2)",
                ),
                GradingPoint(
                    point_id="generated-2b",
                    description="generated scope two second",
                    score=1.0,
                    scope="(2)",
                ),
            ],
        )


@pytest.mark.asyncio
async def test_printed_reference_bundle_collapses_subquestions_under_parent_question():
    perception_engine = _PrintedSubquestionReferencePerception()
    workflow = RubricBundleWorkflow(
        perception_engine=perception_engine,
        skill_service=None,
    )

    bundle = await workflow.generate_from_printed_reference(
        [_make_test_image_bytes()],
        paper_id="paper-printed",
    )

    assert [rubric.question_id for rubric in bundle.rubrics] == ["12"]
    assert "【来源题号】12/(1)" in bundle.rubrics[0].correct_answer
    assert "【来源题号】12/(2)" in bundle.rubrics[0].correct_answer
    assert bundle.rubrics[0].subquestions == ["(1)", "(2)"]
    assert set(bundle.rubrics[0].solution_slots) == {"parent", "(1)", "(2)"}
    assert [part.source_question_no for part in bundle.rubrics[0].reference_evidence_parts] == [
        "12",
        "12/(1)",
        "12/(2)",
    ]
    assert [node.normalized_path for node in bundle.question_tree] == [["12"]]
    assert perception_engine.contexts == ["REFERENCE"]


@pytest.mark.asyncio
async def test_printed_reference_text_enriches_grading_points_with_cognitive_rubric():
    cognitive_agent = _RubricGeneratingCognitiveAgent(score=2.0)
    workflow = RubricBundleWorkflow(
        perception_engine=_PrintedSubquestionReferencePerception(),
        cognitive_agent=cognitive_agent,
    )

    bundle = await workflow.generate_from_printed_reference_text(
        """
        四、实验探究
        18．题干
        (1) 第一问答案（1 分）
        """,
        paper_id="paper-printed-text",
    )

    assert len(cognitive_agent.calls) == 1
    assert any(
        "第一问答案" in element.raw_content
        for element in cognitive_agent.calls[0].elements
    )
    assert bundle.rubrics[0].question_id == "四/18"
    assert [point.point_id for point in bundle.rubrics[0].grading_points] == ["generated-p1"]


@pytest.mark.asyncio
async def test_printed_reference_keeps_deterministic_points_when_generated_score_shrinks():
    cognitive_agent = _RubricGeneratingCognitiveAgent(score=1.0)
    workflow = RubricBundleWorkflow(
        perception_engine=_PrintedSubquestionReferencePerception(),
        cognitive_agent=cognitive_agent,
    )

    bundle = await workflow.generate_from_printed_reference_text(
        """
        四、实验探究
        18．题干
        (1) 第一问答案（2 分）
        """,
        paper_id="paper-printed-text",
    )

    assert len(cognitive_agent.calls) == 1
    assert len(bundle.rubrics[0].grading_points) == 2
    assert sum(point.score for point in bundle.rubrics[0].grading_points) == 2


@pytest.mark.asyncio
async def test_printed_reference_prefers_generated_scope_when_scope_score_is_complete():
    cognitive_agent = _ScopedRubricGeneratingCognitiveAgent()
    workflow = RubricBundleWorkflow(
        perception_engine=_PrintedSubquestionReferencePerception(),
        cognitive_agent=cognitive_agent,
    )

    bundle = await workflow.generate_from_printed_reference_text(
        """
        四、实验探究
        18．题干
        (1) 第一问答案（2 分）
        (2) 第二问第一步（1 分）
        第二问第二步（1 分）
        """,
        paper_id="paper-printed-text",
    )

    points = bundle.rubrics[0].grading_points
    assert [point.point_id for point in points] == [
        "四/18/(1)-p01",
        "四/18/(1)-p02",
        "generated-2a",
        "generated-2b",
    ]


@pytest.mark.asyncio
async def test_handwritten_reference_bundle_groups_subquestions_under_parent_question():
    perception_engine = _HandwrittenReferencePerception()
    workflow = RubricBundleWorkflow(
        perception_engine=perception_engine,
        skill_service=None,
    )

    bundle = await workflow.generate_from_handwritten_reference(
        [_make_test_image_bytes()],
        paper_id="paper-handwritten",
    )

    assert bundle.paper_id == "paper-handwritten"
    assert [rubric.question_id for rubric in bundle.rubrics] == ["12"]
    assert "【来源题号】12/(1)" in bundle.rubrics[0].correct_answer
    assert "【来源题号】12/(2)" in bundle.rubrics[0].correct_answer
    assert "【来源题号】12/(3)" in bundle.rubrics[0].correct_answer
    assert bundle.rubrics[0].subquestions == ["(1)", "(2)", "(3)"]
    assert set(bundle.rubrics[0].solution_slots) == {"parent", "(1)", "(2)", "(3)"}
    assert [part.source_question_no for part in bundle.rubrics[0].reference_evidence_parts] == [
        "12",
        "12/(1)",
        "12/(2)",
        "12/(3)",
    ]
    assert [node.normalized_path for node in bundle.question_tree] == [["12"]]
    assert perception_engine.contexts
    assert all(context == "REFERENCE" for context in perception_engine.contexts)
