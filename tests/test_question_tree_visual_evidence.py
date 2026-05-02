from src.cognitive.question_tree import QuestionTreeExtractor
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput


def test_printed_reference_visual_nodes_are_embedded_as_dense_rubric_text():
    output = PerceptionOutput(
        readability_status="CLEAR",
        global_confidence=1.0,
        elements=[
            PerceptionNode(
                element_id="q1",
                content_type="plain_text",
                raw_content="1．如图所示，判断电路连接方式。",
                confidence_score=1.0,
                bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.2),
            ),
            PerceptionNode(
                element_id="diagram_1",
                content_type="circuit_schematic",
                raw_content="电源、开关和灯泡 L1 串联后，再与灯泡 L2 并联；电流表位于干路。",
                confidence_score=0.98,
                bbox=BoundingBox(x_min=0.2, y_min=0.25, x_max=0.7, y_max=0.5),
            ),
            PerceptionNode(
                element_id="answer",
                content_type="plain_text",
                raw_content="【答案】L1 与 L2 并联",
                confidence_score=1.0,
                bbox=BoundingBox(x_min=0.1, y_min=0.55, x_max=0.8, y_max=0.65),
            ),
        ],
    )

    bundle = QuestionTreeExtractor().extract_from_perception(output, paper_id="paper-visual")

    assert len(bundle.rubrics) == 1
    rubric = bundle.rubrics[0]
    assert "如图所示" in rubric.correct_answer
    assert "【图表描述:diagram_1|circuit_schematic】" in rubric.correct_answer
    assert "电源、开关和灯泡 L1 串联" in rubric.correct_answer
    assert rubric.visual_evidence[0].evidence_type == "circuit_schematic"
    assert rubric.visual_evidence[0].description == "电源、开关和灯泡 L1 串联后，再与灯泡 L2 并联；电流表位于干路。"
    assert rubric.visual_evidence[0].source_element_id == "diagram_1"


def test_printed_reference_markdown_image_assets_are_preserved_as_evidence():
    markdown = """1．观察下列波形图，判断调制方式。
![](images/waveform-a.jpg)
【答案】调频
"""

    bundle = QuestionTreeExtractor().extract_from_markdown(markdown, paper_id="paper-markdown-image")

    assert len(bundle.rubrics) == 1
    rubric = bundle.rubrics[0]
    assert "![](images/waveform-a.jpg)" in rubric.correct_answer
    assert rubric.visual_evidence[0].evidence_type == "image_asset"
    assert rubric.visual_evidence[0].asset_ref == "images/waveform-a.jpg"


def test_markdown_image_descriptions_are_injected_into_rubric_text_and_evidence():
    markdown = """1．观察下列波形图，判断调制方式。
![](images/waveform-a.jpg)
【答案】调频
"""

    bundle = QuestionTreeExtractor().extract_from_markdown(
        markdown,
        paper_id="paper-markdown-image",
        image_descriptions={
            "images/waveform-a.jpg": "图中载波频率随调制信号变化，振幅基本保持不变。"
        },
    )

    rubric = bundle.rubrics[0]
    assert "【图表描述:images/waveform-a.jpg|image_asset】图中载波频率随调制信号变化" in rubric.correct_answer
    assert rubric.visual_evidence[0].evidence_type == "image_asset"
    assert rubric.visual_evidence[0].asset_ref == "images/waveform-a.jpg"
    assert rubric.visual_evidence[0].description == "图中载波频率随调制信号变化，振幅基本保持不变。"
