import asyncio
import io

import pytest
from PIL import Image

from src.cognitive.mock_agent import MockCognitiveAgent
from src.core.config import settings
from src.orchestration.paper_workflow import PaperGradingWorkflow
from src.orchestration.segmentation import AnswerRegionSplitter
from src.perception.mock_engine import MockPerceptionEngine
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput, QuestionAnchorSet
from src.schemas.rubric_ir import RubricBundle, TeacherRubric
from src.skills.interfaces import LayoutParseResult, LayoutRegion


def _make_test_image_bytes() -> bytes:
    img = Image.new("RGB", (128, 128), color=(255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_answer_region_splitter_uses_explicit_layout_question_regions():
    result = AnswerRegionSplitter().split_document(
        [_make_test_image_bytes()],
        anchor_sets=[
            QuestionAnchorSet(
                page_index=0,
                anchors=[],
            )
        ],
        layout_results=[
            LayoutParseResult(
                context_type="STUDENT_ANSWER",
                page_index=0,
                regions=[
                    LayoutRegion(
                        target_id="q1-region",
                        region_type="question_region",
                        question_no="1",
                        bbox={"x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.4},
                    ),
                    LayoutRegion(
                        target_id="q2-region",
                        region_type="question_region",
                        question_no="2",
                        bbox={"x_min": 0.5, "y_min": 0.1, "x_max": 0.8, "y_max": 0.4},
                    ),
                ],
            )
        ],
    )

    assert [region.question_no for region in result.regions] == ["1", "2"]
    assert result.regions[0].bbox.x_min == 0.1
    assert result.regions[1].bbox.x_min == 0.5


class FakeSkillService:
    def __init__(self, layout_results: list[LayoutParseResult]) -> None:
        self._layout_results = layout_results

    async def try_parse_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        page_index: int = 0,
        target_question_no: str | None = None,
    ) -> LayoutParseResult | None:
        del image_bytes, context_type, target_question_no
        return self._layout_results[page_index]


class CountingMockPerceptionEngine(MockPerceptionEngine):
    def __init__(self) -> None:
        self.calls = 0

    async def process_image(self, image_bytes: bytes):
        self.calls += 1
        return await super().process_image(image_bytes)

    async def process_images(self, image_bytes_list: list[bytes], *, context_type: str = "student_homework"):
        if context_type == "student_answer_regions":
            self.calls += len(image_bytes_list)
        return await super().process_images(image_bytes_list, context_type=context_type)


class BatchPerceptionEngine(MockPerceptionEngine):
    def __init__(self) -> None:
        self.single_calls = 0
        self.batch_calls: list[tuple[str, int]] = []

    async def process_image(self, image_bytes: bytes):
        del image_bytes
        self.single_calls += 1
        raise AssertionError("paper workflow should use batch perception")

    async def process_images(self, image_bytes_list: list[bytes], *, context_type: str = "student_homework"):
        self.batch_calls.append((context_type, len(image_bytes_list)))
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
                            bbox=BoundingBox(x_min=0.1, y_min=0.5, x_max=0.2, y_max=0.55),
                        ),
                    ],
                    global_confidence=1.0,
                )
                for _ in image_bytes_list
            ]
        return [
            PerceptionOutput(
                readability_status="CLEAR",
                elements=[
                    PerceptionNode(
                        element_id=f"answer-{index}",
                        content_type="plain_text",
                        raw_content=f"<student>answer {index}</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.2),
                    )
                ],
                global_confidence=1.0,
            )
            for index, _ in enumerate(image_bytes_list, start=1)
        ]


class ConcurrentSingleImagePerceptionEngine(MockPerceptionEngine):
    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def process_images(self, image_bytes_list: list[bytes], *, context_type: str = "student_homework"):
        if context_type != "student_answer_regions":
            return await super().process_images(image_bytes_list, context_type=context_type)
        assert len(image_bytes_list) == 1
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            content = image_bytes_list[0].decode("utf-8")
            return [
                PerceptionOutput(
                    readability_status="CLEAR",
                    elements=[
                        PerceptionNode(
                            element_id=f"answer-{content}",
                            content_type="plain_text",
                            raw_content=f"<student>{content}</student>",
                            confidence_score=1.0,
                            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.2),
                        )
                    ],
                    global_confidence=1.0,
                )
            ]
        finally:
            self.in_flight -= 1


class ConcurrentBatchPerceptionEngine(MockPerceptionEngine):
    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.batch_sizes: list[int] = []

    async def process_images(self, image_bytes_list: list[bytes], *, context_type: str = "student_homework"):
        if context_type != "student_answer_regions":
            return await super().process_images(image_bytes_list, context_type=context_type)
        self.batch_sizes.append(len(image_bytes_list))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return [
                PerceptionOutput(
                    readability_status="CLEAR",
                    elements=[
                        PerceptionNode(
                            element_id=f"answer-{image_bytes.decode('utf-8')}",
                            content_type="plain_text",
                            raw_content=f"<student>{image_bytes.decode('utf-8')}</student>",
                            confidence_score=1.0,
                            bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.2),
                        )
                    ],
                    global_confidence=1.0,
                )
                for image_bytes in image_bytes_list
            ]
        finally:
            self.in_flight -= 1


class FallbackEventPerceptionEngine(BatchPerceptionEngine):
    def __init__(self) -> None:
        super().__init__()
        self._events = [
            {
                "context_type": "student_answer_regions",
                "image_count": 2,
                "reason": "simulated timeout",
            }
        ]

    def drain_batch_fallback_events(self):
        events = list(self._events)
        self._events.clear()
        return events


class OverlapPerceptionEngine(MockPerceptionEngine):
    def __init__(self) -> None:
        self._answer_output_offset = 0

    async def process_images(self, image_bytes_list: list[bytes], *, context_type: str = "student_homework"):
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
                            bbox=BoundingBox(x_min=0.1, y_min=0.5, x_max=0.2, y_max=0.55),
                        ),
                    ],
                    global_confidence=1.0,
                )
            ]
        answer_outputs = [
            PerceptionOutput(
                readability_status="CLEAR",
                elements=[
                    PerceptionNode(
                        element_id="q1-text",
                        content_type="plain_text",
                        raw_content="1. 第一题内容",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.10, x_max=0.8, y_max=0.16),
                    ),
                    PerceptionNode(
                        element_id="q2-overlap",
                        content_type="plain_text",
                        raw_content="2. 第二题题干被重叠截入",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.50, x_max=0.8, y_max=0.56),
                    ),
                    PerceptionNode(
                        element_id="q1-student-late",
                        content_type="plain_text",
                        raw_content="<student>A</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.05, y_min=0.12, x_max=0.09, y_max=0.18),
                    ),
                    PerceptionNode(
                        element_id="q2-student-overlap",
                        content_type="plain_text",
                        raw_content="<student>B</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.05, y_min=0.50, x_max=0.09, y_max=0.56),
                    ),
                ],
                global_confidence=1.0,
            ),
            PerceptionOutput(
                readability_status="CLEAR",
                elements=[
                    PerceptionNode(
                        element_id="q1-tail",
                        content_type="plain_text",
                        raw_content="上一题尾部",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.05, x_max=0.8, y_max=0.10),
                    ),
                    PerceptionNode(
                        element_id="q2-text",
                        content_type="plain_text",
                        raw_content="2. 第二题内容",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.20, x_max=0.8, y_max=0.26),
                    ),
                    PerceptionNode(
                        element_id="q3-overlap",
                        content_type="plain_text",
                        raw_content="3. 第三题题干被重叠截入",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.75, x_max=0.8, y_max=0.82),
                    ),
                    PerceptionNode(
                        element_id="q2-student-late",
                        content_type="plain_text",
                        raw_content="<student>B</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.05, y_min=0.22, x_max=0.09, y_max=0.28),
                    ),
                ],
                global_confidence=1.0,
            ),
        ]
        start = self._answer_output_offset
        self._answer_output_offset += len(image_bytes_list)
        return answer_outputs[start : start + len(image_bytes_list)]


class OCRInferenceWithoutStudentTagsEngine(MockPerceptionEngine):
    async def process_images(self, image_bytes_list: list[bytes], *, context_type: str = "student_homework"):
        if context_type != "student_answer_regions":
            return await super().process_images(image_bytes_list, context_type=context_type)
        return [
            PerceptionOutput(
                readability_status="CLEAR",
                elements=[
                    PerceptionNode(
                        element_id="answer-q18",
                        content_type="plain_text",
                        raw_content="18. 解:(1) T=\\frac{4}{7}\\pi\nT=2\\pi\\sqrt{\\frac{L}{g}}",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.9, y_max=0.9),
                    )
                ],
                global_confidence=1.0,
            )
            for _ in image_bytes_list
        ]


class CapturingCognitiveAgent(MockCognitiveAgent):
    def __init__(self) -> None:
        self.seen_text_by_question: dict[str, str] = {}

    async def evaluate_logic(self, perception_data: PerceptionOutput, rubric=None):
        self.seen_text_by_question[rubric.question_id] = "\n".join(
            element.raw_content for element in perception_data.elements
        )
        return await super().evaluate_logic(perception_data, rubric)


def test_numeric_equivalence_contradiction_triggers_review_gate():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )
    report = EvaluationReport(
        status="SCORED",
        is_fully_correct=False,
        total_score_deduction=2.0,
        step_evaluations=[
            {
                "reference_element_id": "p0_answer_18_part0_0_1",
                "is_correct": False,
                "error_type": "CALCULATION",
                "correction_suggestion": "右侧数值0.80m/0.800m不成立。",
            }
        ],
        overall_feedback="未正确从半周期推得周期。",
        system_confidence=0.9,
        requires_human_review=False,
    )

    workflow._apply_numeric_equivalence_quality_gate(report)

    assert report.requires_human_review is True
    assert report.system_confidence == 0.5
    assert "0.80 与 0.800" in report.overall_feedback


@pytest.mark.asyncio
async def test_paper_workflow_grades_each_rubric_question_from_split_regions():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
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
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-1",
            rubrics=[
                TeacherRubric(question_id="1", correct_answer="A"),
                TeacherRubric(question_id="2", correct_answer="B"),
            ],
            question_tree=[],
        ),
    )

    assert report.paper_id == "paper-1"
    assert report.total_questions == 2
    assert report.answered_questions == 2
    assert report.total_score_deduction == 4.0
    assert set(report.per_question.keys()) == {"1", "2"}
    assert report.requires_human_review is False


@pytest.mark.asyncio
async def test_paper_workflow_flags_missing_question_regions_for_review():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
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
                    ],
                )
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-2",
            rubrics=[
                TeacherRubric(question_id="1", correct_answer="A"),
                TeacherRubric(question_id="2", correct_answer="B"),
            ],
            question_tree=[],
        ),
    )

    assert report.answered_questions == 1
    assert report.requires_human_review is True
    assert report.per_question["2"].status == "REJECTED_UNREADABLE"
    assert any("question 2: no answer region matched" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_paper_workflow_does_not_raise_paper_review_for_student_tag_inference_warnings():
    workflow = PaperGradingWorkflow(
        perception_engine=OCRInferenceWithoutStudentTagsEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )

    report = await workflow.run_pipeline_with_presegmented_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-inferred-student-tags",
            rubrics=[TeacherRubric(question_id="18", correct_answer="T=4π/7")],
            question_tree=[],
        ),
        presegmented_question_ids=["18"],
    )

    assert report.requires_human_review is False
    assert "question 18: ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS" in report.warnings
    assert report.per_question["18"].status == "SCORED"


@pytest.mark.asyncio
async def test_paper_workflow_uses_parent_region_for_unanchored_subquestion():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
                    regions=[
                        LayoutRegion(
                            target_id="q16",
                            region_type="title",
                            question_no="16.",
                            bbox={"x_min": 0.10, "y_min": 0.10, "x_max": 0.20, "y_max": 0.15},
                        ),
                        LayoutRegion(
                            target_id="q16-body",
                            region_type="text",
                            bbox={"x_min": 0.08, "y_min": 0.10, "x_max": 0.80, "y_max": 0.70},
                        ),
                    ],
                )
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-subquestion",
            rubrics=[TeacherRubric(question_id="16/(2)", correct_answer="B")],
            question_tree=[],
        ),
    )

    assert report.answered_questions == 1
    assert report.per_question["16/(2)"].status == "SCORED"
    assert any("question 16/(2): using ancestor answer region 16" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_paper_workflow_uses_previous_sibling_region_for_merged_subquestion():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
                    regions=[
                        LayoutRegion(
                            target_id="q16",
                            region_type="title",
                            question_no="16.",
                            bbox={"x_min": 0.10, "y_min": 0.10, "x_max": 0.20, "y_max": 0.15},
                        ),
                        LayoutRegion(
                            target_id="sub1",
                            region_type="title",
                            question_no="(1)",
                            bbox={"x_min": 0.10, "y_min": 0.20, "x_max": 0.20, "y_max": 0.25},
                        ),
                        LayoutRegion(
                            target_id="q16-body",
                            region_type="text",
                            bbox={"x_min": 0.08, "y_min": 0.20, "x_max": 0.80, "y_max": 0.70},
                        ),
                    ],
                )
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-merged-subquestion",
            rubrics=[TeacherRubric(question_id="16/(2)", correct_answer="B")],
            question_tree=[],
        ),
    )

    assert report.answered_questions == 1
    assert report.per_question["16/(2)"].status == "SCORED"
    assert any("question 16/(2): using ancestor answer region 16/(1)" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_paper_workflow_parent_rubric_uses_parent_and_subquestion_regions():
    perception_engine = CountingMockPerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
                    regions=[
                        LayoutRegion(
                            target_id="q16",
                            region_type="title",
                            question_no="16.",
                            bbox={"x_min": 0.10, "y_min": 0.10, "x_max": 0.20, "y_max": 0.15},
                        ),
                        LayoutRegion(
                            target_id="sub1",
                            region_type="title",
                            question_no="(1)",
                            bbox={"x_min": 0.10, "y_min": 0.20, "x_max": 0.20, "y_max": 0.25},
                        ),
                        LayoutRegion(
                            target_id="sub2",
                            region_type="title",
                            question_no="(2)",
                            bbox={"x_min": 0.10, "y_min": 0.45, "x_max": 0.20, "y_max": 0.50},
                        ),
                    ],
                )
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-parent-question",
            rubrics=[TeacherRubric(question_id="16", correct_answer="(1)A (2)B")],
            question_tree=[],
        ),
    )

    assert report.answered_questions == 1
    assert report.per_question["16"].status == "SCORED"
    assert not any("answer region has no matching rubric" in warning for warning in report.warnings)
    assert perception_engine.calls == 4


@pytest.mark.asyncio
async def test_paper_workflow_does_not_force_parent_subquestion_slots_for_freeform_answer():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
                    regions=[
                        LayoutRegion(
                            target_id="q18",
                            region_type="title",
                            question_no="18.",
                            bbox={"x_min": 0.10, "y_min": 0.10, "x_max": 0.20, "y_max": 0.15},
                        ),
                    ],
                )
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-slot-contract",
            rubrics=[
                TeacherRubric(
                    question_id="18",
                    correct_answer="(1)A (2)B (3)C",
                    subquestions=["(1)", "(2)", "(3)"],
                    context_stem_text="18. shared stem",
                )
            ],
            question_tree=[],
        ),
    )

    assert report.student_answer_bundle is not None
    answer = report.student_answer_bundle.answers[0]
    assert answer.question_id == "18"
    assert answer.stem_scope == "18. shared stem"
    assert answer.slot_answers == {}


def test_paper_workflow_infers_fill_blank_slots_from_rubric_text():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )

    slots = workflow._expected_slots_by_question(
        RubricBundle(
            paper_id="paper-fill",
            rubrics=[
                TeacherRubric(
                    question_id="2",
                    correct_answer="2．______，______，______，______。【答案】调制 调幅 调谐 解调",
                )
            ],
            question_tree=[],
        )
    )

    assert slots == {"2": ["blank_1", "blank_2", "blank_3", "blank_4"]}


def test_paper_workflow_does_not_force_slots_from_parent_rubric_subquestions():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )

    slots = workflow._expected_slots_by_question(
        RubricBundle(
            paper_id="paper-freeform",
            rubrics=[
                TeacherRubric(
                    question_id="18",
                    correct_answer="(1)A (2)B (3)C",
                    subquestions=["(1)", "(2)", "(3)"],
                )
            ],
            question_tree=[],
        )
    )

    assert slots == {"18": []}


def test_prepare_answer_region_image_preserves_wide_crop_when_downscale_harms_short_side():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )
    image = Image.new("RGB", (2509, 631), color=(255, 255, 255))
    source = io.BytesIO()
    image.save(source, format="PNG")

    prepared = workflow._prepare_answer_region_image(source.getvalue())

    with Image.open(io.BytesIO(prepared)) as prepared_image:
        assert prepared_image.size == (2509, 631)
        assert prepared_image.format == "PNG"


def test_prepare_answer_region_image_downscales_large_square_crop():
    workflow = PaperGradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )
    image = Image.new("RGB", (2500, 2500), color=(255, 255, 255))
    source = io.BytesIO()
    image.save(source, format="PNG")

    prepared = workflow._prepare_answer_region_image(source.getvalue())

    with Image.open(io.BytesIO(prepared)) as prepared_image:
        assert max(prepared_image.size) <= settings.qwen_answer_region_max_side
        assert prepared_image.format == "JPEG"


@pytest.mark.asyncio
async def test_paper_workflow_batches_student_answer_perception_before_grading(monkeypatch):
    monkeypatch.setattr(settings, "qwen_answer_region_strategy", "fixed")
    monkeypatch.setattr(settings, "qwen_batch_max_images", 2)
    perception_engine = BatchPerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
                    regions=[
                        LayoutRegion(
                            target_id="q1",
                            region_type="title",
                            question_no="1.",
                            bbox={"x_min": 0.10, "y_min": 0.10, "x_max": 0.20, "y_max": 0.15},
                        ),
                        LayoutRegion(
                            target_id="q2",
                            region_type="title",
                            question_no="2.",
                            bbox={"x_min": 0.10, "y_min": 0.55, "x_max": 0.20, "y_max": 0.60},
                        ),
                    ],
                )
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-batch",
            rubrics=[
                TeacherRubric(question_id="1", correct_answer="A"),
                TeacherRubric(question_id="2", correct_answer="B"),
            ],
            question_tree=[],
        ),
    )

    assert report.answered_questions == 2
    assert report.student_answer_bundle is not None
    assert [answer.question_id for answer in report.student_answer_bundle.answers] == ["1", "2"]
    assert perception_engine.single_calls == 0
    assert perception_engine.batch_calls == [
        ("student_paper_pages", 1),
        ("student_answer_regions", 2),
    ]


@pytest.mark.asyncio
async def test_paper_workflow_concurrently_processes_single_answer_region_calls(monkeypatch):
    monkeypatch.setattr(settings, "qwen_answer_region_strategy", "fixed")
    monkeypatch.setattr(settings, "qwen_batch_max_images", 1)
    monkeypatch.setattr(settings, "qwen_single_image_concurrency", 2)
    monkeypatch.setattr(settings, "qwen_answer_region_batch_concurrency", 1)
    perception_engine = ConcurrentSingleImagePerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
    )

    outputs = await workflow._process_images_in_chunks(
        [b"answer-1", b"answer-2", b"answer-3", b"answer-4"],
        context_type="student_answer_regions",
    )

    assert perception_engine.calls == 4
    assert perception_engine.max_in_flight == 2
    assert [
        output.elements[0].raw_content
        for output in outputs
    ] == [
        "<student>answer-1</student>",
        "<student>answer-2</student>",
        "<student>answer-3</student>",
        "<student>answer-4</student>",
    ]


@pytest.mark.asyncio
async def test_paper_workflow_concurrently_processes_answer_region_batches(monkeypatch):
    monkeypatch.setattr(settings, "qwen_answer_region_strategy", "fixed")
    monkeypatch.setattr(settings, "qwen_batch_max_images", 3)
    monkeypatch.setattr(settings, "qwen_single_image_concurrency", 1)
    monkeypatch.setattr(settings, "qwen_answer_region_batch_concurrency", 2)
    perception_engine = ConcurrentBatchPerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
    )

    outputs = await workflow._process_images_in_chunks(
        [b"answer-1", b"answer-2", b"answer-3", b"answer-4", b"answer-5", b"answer-6"],
        context_type="student_answer_regions",
    )

    assert perception_engine.batch_sizes == [3, 3]
    assert perception_engine.max_in_flight == 2
    assert [
        output.elements[0].raw_content
        for output in outputs
    ] == [
        "<student>answer-1</student>",
        "<student>answer-2</student>",
        "<student>answer-3</student>",
        "<student>answer-4</student>",
        "<student>answer-5</student>",
        "<student>answer-6</student>",
    ]


@pytest.mark.asyncio
async def test_paper_workflow_auto_planner_uses_two_image_batches_for_mid_sized_inputs(monkeypatch):
    monkeypatch.setattr(settings, "qwen_answer_region_strategy", "auto")
    monkeypatch.setattr(settings, "qwen_batch_max_images", 3)
    monkeypatch.setattr(settings, "qwen_answer_region_batch_concurrency", 3)
    monkeypatch.setattr(settings, "qwen_api_max_concurrency", 3)
    perception_engine = ConcurrentBatchPerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
    )

    outputs = await workflow._process_images_in_chunks(
        [f"answer-{index}".encode("utf-8") for index in range(1, 7)],
        context_type="student_answer_regions",
    )

    assert perception_engine.batch_sizes == [2, 2, 2]
    assert perception_engine.max_in_flight == 2
    assert [output.elements[0].raw_content for output in outputs] == [
        f"<student>answer-{index}</student>"
        for index in range(1, 7)
    ]


@pytest.mark.asyncio
async def test_paper_workflow_auto_planner_uses_three_concurrent_slots_for_large_inputs(monkeypatch):
    monkeypatch.setattr(settings, "qwen_answer_region_strategy", "auto")
    monkeypatch.setattr(settings, "qwen_batch_max_images", 2)
    monkeypatch.setattr(settings, "qwen_answer_region_batch_concurrency", 3)
    monkeypatch.setattr(settings, "qwen_api_max_concurrency", 3)
    perception_engine = ConcurrentBatchPerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
    )

    outputs = await workflow._process_images_in_chunks(
        [f"answer-{index}".encode("utf-8") for index in range(1, 11)],
        context_type="student_answer_regions",
    )

    assert perception_engine.batch_sizes == [2, 2, 2, 2, 2]
    assert perception_engine.max_in_flight == 3
    assert len(outputs) == 10


@pytest.mark.asyncio
async def test_paper_workflow_auto_planner_scales_with_qwen_key_count(monkeypatch):
    monkeypatch.setattr(settings, "qwen_answer_region_strategy", "auto")
    monkeypatch.setattr(settings, "qwen_batch_max_images", 2)
    monkeypatch.setattr(settings, "qwen_answer_region_batch_concurrency", 8)
    monkeypatch.setattr(settings, "qwen_api_max_concurrency", 0)
    monkeypatch.setattr(settings, "qwen_api_auto_max_concurrency", 8)
    monkeypatch.setattr(settings, "qwen_api_keys", "k1,k2,k3,k4,k5")
    monkeypatch.setattr(settings, "qwen_api_key", None)
    perception_engine = ConcurrentBatchPerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
    )

    outputs = await workflow._process_images_in_chunks(
        [f"answer-{index}".encode("utf-8") for index in range(1, 11)],
        context_type="student_answer_regions",
    )

    assert settings.effective_qwen_api_max_concurrency == 5
    assert perception_engine.batch_sizes == [2, 2, 2, 2, 2]
    assert perception_engine.max_in_flight == 4
    assert len(outputs) == 10


@pytest.mark.asyncio
async def test_paper_workflow_auto_planner_caps_very_large_inputs(monkeypatch):
    monkeypatch.setattr(settings, "qwen_answer_region_strategy", "auto")
    monkeypatch.setattr(settings, "qwen_batch_max_images", 2)
    monkeypatch.setattr(settings, "qwen_answer_region_batch_concurrency", 8)
    monkeypatch.setattr(settings, "qwen_api_max_concurrency", 0)
    monkeypatch.setattr(settings, "qwen_api_auto_max_concurrency", 8)
    monkeypatch.setattr(settings, "qwen_api_keys", ",".join(f"k{index}" for index in range(1, 21)))
    monkeypatch.setattr(settings, "qwen_api_key", None)
    perception_engine = ConcurrentBatchPerceptionEngine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
    )

    outputs = await workflow._process_images_in_chunks(
        [f"answer-{index}".encode("utf-8") for index in range(1, 21)],
        context_type="student_answer_regions",
    )

    assert settings.effective_qwen_api_max_concurrency == 8
    assert perception_engine.batch_sizes == [2] * 10
    assert perception_engine.max_in_flight == 6
    assert len(outputs) == 20


@pytest.mark.asyncio
async def test_paper_workflow_surfaces_qwen_batch_fallback_warnings():
    workflow = PaperGradingWorkflow(
        perception_engine=FallbackEventPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
                    regions=[
                        LayoutRegion(
                            target_id="q1",
                            region_type="title",
                            question_no="1.",
                            bbox={"x_min": 0.10, "y_min": 0.10, "x_max": 0.20, "y_max": 0.15},
                        ),
                    ],
                )
            ]
        ),
    )

    report = await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-fallback",
            rubrics=[TeacherRubric(question_id="1", correct_answer="A")],
            question_tree=[],
        ),
    )

    assert any("qwen batch fallback" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_paper_workflow_trims_overlapped_adjacent_question_text_before_grading():
    cognitive_agent = CapturingCognitiveAgent()
    workflow = PaperGradingWorkflow(
        perception_engine=OverlapPerceptionEngine(),
        cognitive_agent=cognitive_agent,
        skill_service=FakeSkillService(
            [
                LayoutParseResult(
                    context_type="STUDENT_ANSWER",
                    page_index=0,
                    regions=[
                        LayoutRegion(
                            target_id="page",
                            region_type="text",
                            bbox={"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0},
                        )
                    ],
                )
            ]
        ),
    )

    await workflow.run_pipeline_with_preprocessed_images(
        [_make_test_image_bytes()],
        RubricBundle(
            paper_id="paper-overlap",
            rubrics=[
                TeacherRubric(question_id="1", correct_answer="A"),
                TeacherRubric(question_id="2", correct_answer="B"),
            ],
            question_tree=[],
        ),
    )

    assert "第二题题干被重叠截入" not in cognitive_agent.seen_text_by_question["1"]
    assert "A" in cognitive_agent.seen_text_by_question["1"]
    assert "<student>A</student>" in cognitive_agent.seen_text_by_question["1"]
    assert "<student>B</student>" not in cognitive_agent.seen_text_by_question["1"]
    assert "上一题尾部" not in cognitive_agent.seen_text_by_question["2"]
    assert "第三题题干被重叠截入" not in cognitive_agent.seen_text_by_question["2"]
    assert "B" in cognitive_agent.seen_text_by_question["2"]
    assert "<student>B</student>" in cognitive_agent.seen_text_by_question["2"]
