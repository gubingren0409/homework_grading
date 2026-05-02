from src.perception.question_anchor import QuestionAnchorDetector
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput
from src.skills.interfaces import LayoutParseResult, LayoutRegion


def test_question_anchor_detector_extracts_hierarchical_perception_anchors():
    detector = QuestionAnchorDetector()
    output = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=0.95,
        is_blank=False,
        trigger_short_circuit=False,
        elements=[
            PerceptionNode(
                element_id="e1",
                content_type="plain_text",
                raw_content="一、选择题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.15),
            ),
            PerceptionNode(
                element_id="e2",
                content_type="plain_text",
                raw_content="1. 关于电场，下列说法正确的是",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.12, y_min=0.2, x_max=0.5, y_max=0.25),
            ),
            PerceptionNode(
                element_id="e3",
                content_type="plain_text",
                raw_content="（1）求电场强度",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.16, y_min=0.3, x_max=0.45, y_max=0.34),
            ),
        ],
    )

    anchors = detector.detect_from_perception(output, page_index=2)

    assert [anchor.question_no for anchor in anchors.anchors] == ["一", "一/1", "一/1/(1)"]
    assert anchors.anchors[1].page_index == 2
    assert anchors.anchors[2].source == "perception"


def test_question_anchor_detector_ignores_non_anchor_numbers():
    detector = QuestionAnchorDetector()
    output = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=0.95,
        is_blank=False,
        trigger_short_circuit=False,
        elements=[
            PerceptionNode(
                element_id="e1",
                content_type="plain_text",
                raw_content="电压为89.9V，电流为1.3A",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.15),
            ),
        ],
    )

    anchors = detector.detect_from_perception(output)

    assert anchors.anchors == []


def test_question_anchor_detector_reads_layout_question_numbers():
    detector = QuestionAnchorDetector()
    layout = LayoutParseResult(
        context_type="STUDENT_ANSWER",
        page_index=1,
        regions=[
            LayoutRegion(
                target_id="r2",
                region_type="title",
                question_no="（1）",
                bbox={"x_min": 0.2, "y_min": 0.3, "x_max": 0.4, "y_max": 0.35},
            ),
            LayoutRegion(
                target_id="r1",
                region_type="title",
                question_no="2．",
                bbox={"x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.15},
            ),
        ],
    )

    anchors = detector.detect_from_layout(layout)

    assert [anchor.question_no for anchor in anchors.anchors] == ["2", "2/(1)"]
    assert anchors.anchors[0].source == "layout"


def test_question_anchor_detector_carries_section_parent_across_pages():
    detector = QuestionAnchorDetector()
    page_one = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=0.95,
        is_blank=False,
        trigger_short_circuit=False,
        elements=[
            PerceptionNode(
                element_id="p0_s1",
                content_type="plain_text",
                raw_content="一、选择题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.15),
            ),
            PerceptionNode(
                element_id="p0_q4",
                content_type="plain_text",
                raw_content="4．第四题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.7, x_max=0.3, y_max=0.75),
            ),
        ],
    )
    page_two = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=0.95,
        is_blank=False,
        trigger_short_circuit=False,
        elements=[
            PerceptionNode(
                element_id="p1_q5",
                content_type="plain_text",
                raw_content="5．第五题跨页续接",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.15),
            ),
            PerceptionNode(
                element_id="p1_s2",
                content_type="plain_text",
                raw_content="二、实验题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.3, x_max=0.4, y_max=0.35),
            ),
            PerceptionNode(
                element_id="p1_q6",
                content_type="plain_text",
                raw_content="6．第六题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.4, x_max=0.4, y_max=0.45),
            ),
        ],
    )

    anchor_sets = detector.detect_document_from_perceptions([page_one, page_two])

    assert [anchor.question_no for anchor in anchor_sets[0].anchors] == ["一", "一/4"]
    assert [anchor.question_no for anchor in anchor_sets[1].anchors] == ["一/5", "二", "二/6"]


def test_question_anchor_detector_ignores_false_large_numeric_jumps():
    detector = QuestionAnchorDetector()
    output = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=0.95,
        is_blank=False,
        trigger_short_circuit=False,
        elements=[
            PerceptionNode(
                element_id="s3",
                content_type="plain_text",
                raw_content="三、电磁振荡",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.15),
            ),
            PerceptionNode(
                element_id="q12",
                content_type="plain_text",
                raw_content="12．第十二题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.2, x_max=0.3, y_max=0.25),
            ),
            PerceptionNode(
                element_id="q13",
                content_type="plain_text",
                raw_content="13．第十三题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.3, x_max=0.3, y_max=0.35),
            ),
            PerceptionNode(
                element_id="q23",
                content_type="plain_text",
                raw_content="23．详解步骤，不是新题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.4, x_max=0.4, y_max=0.45),
            ),
        ],
    )

    anchors = detector.detect_from_perception(output)

    assert [anchor.question_no for anchor in anchors.anchors] == ["三", "三/12", "三/13"]


def test_question_anchor_detector_ignores_duplicate_subquestion_anchor():
    detector = QuestionAnchorDetector()
    output = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=0.95,
        is_blank=False,
        trigger_short_circuit=False,
        elements=[
            PerceptionNode(
                element_id="s4",
                content_type="plain_text",
                raw_content="四、实验探究",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.15),
            ),
            PerceptionNode(
                element_id="q16",
                content_type="plain_text",
                raw_content="16．第十六题",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.1, y_min=0.2, x_max=0.3, y_max=0.25),
            ),
            PerceptionNode(
                element_id="sub2",
                content_type="plain_text",
                raw_content="(2)第二小问",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.12, y_min=0.3, x_max=0.4, y_max=0.35),
            ),
            PerceptionNode(
                element_id="sub2-answer",
                content_type="plain_text",
                raw_content="（2）第二小问详解",
                confidence_score=0.9,
                bbox=BoundingBox(x_min=0.12, y_min=0.4, x_max=0.4, y_max=0.45),
            ),
        ],
    )

    anchors = detector.detect_from_perception(output)

    assert [anchor.question_no for anchor in anchors.anchors] == ["四", "四/16", "四/16/(2)"]
