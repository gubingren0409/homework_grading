import json
from pathlib import Path

import pytest

from src.orchestration.student_answer_bundle import (
    build_student_answer_bundle,
    student_answer_to_perception_output,
)
from src.schemas.answer_ir import StudentAnswerPart
from src.schemas.perception_ir import BoundingBox, PerceptionNode


def test_student_answer_bundle_groups_parts_by_parent_question_id():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"12": ["(1)", "(2)"]},
        parts=[
            StudentAnswerPart(
                source_question_no="12/(1)",
                crop_index=1,
                page_index=0,
                bbox=BoundingBox(x_min=0.0, y_min=0.1, x_max=1.0, y_max=0.3),
                text="(1) printed stem <student>A</student>",
                elements=[
                    PerceptionNode(
                        element_id="e1",
                        content_type="plain_text",
                        raw_content="<student>A</student>",
                        confidence_score=0.9,
                    )
                ],
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
            StudentAnswerPart(
                source_question_no="12/(2)",
                crop_index=2,
                page_index=0,
                bbox=BoundingBox(x_min=0.0, y_min=0.3, x_max=1.0, y_max=0.5),
                text="(2) <student>B</student>",
                elements=[],
                global_confidence=0.8,
                readability_status="CLEAR",
            ),
            StudentAnswerPart(
                source_question_no="13",
                crop_index=3,
                page_index=1,
                bbox=BoundingBox(x_min=0.0, y_min=0.1, x_max=1.0, y_max=0.3),
                text="<student>C</student>",
                elements=[],
                global_confidence=1.0,
                readability_status="CLEAR",
            ),
        ],
    )

    assert bundle.paper_id == "student-paper"
    assert [answer.question_id for answer in bundle.answers] == ["12", "13"]
    assert len(bundle.answers[0].parts) == 2
    assert "【来源题号】12/(1)" in bundle.answers[0].answer_text
    assert "printed stem" not in bundle.answers[0].answer_text
    assert "<student>" not in bundle.answers[0].answer_text
    assert "A" in bundle.answers[0].answer_text
    assert "B" in bundle.answers[0].answer_text
    assert "printed stem" in bundle.answers[0].ocr_text
    assert bundle.answers[0].global_confidence == pytest.approx(0.85)
    assert bundle.answers[0].answer_parts == bundle.answers[0].parts
    assert bundle.answers[0].slot_answers == {"(1)": "A", "(2)": "B"}
    assert [node.normalized_path for node in bundle.question_tree] == [["12"], ["13"]]


def test_student_answer_bundle_warns_when_no_student_tags_found():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        parts=[
            StudentAnswerPart(
                source_question_no="1",
                text="1. printed stem only",
                global_confidence=0.9,
                readability_status="CLEAR",
            )
        ],
    )

    assert bundle.answers[0].answer_text == ""
    assert bundle.answers[0].ocr_text == "【来源题号】1\n1. printed stem only"
    assert bundle.answers[0].is_blank is True
    assert bundle.answers[0].extraction_warnings == ["NO_STUDENT_TAGS_FOUND"]


def test_student_answer_bundle_keeps_missing_slot_explicit_without_shifting():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"18": ["(1)", "(2)", "(3)"]},
        parts=[
            StudentAnswerPart(
                source_question_no="18/(1)",
                text="<student>T=2s</student>",
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
            StudentAnswerPart(
                source_question_no="18/(3)",
                text="<student>m=0.08kg</student>",
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
        ],
    )

    assert bundle.answers[0].slot_answers == {
        "(1)": "T=2s",
        "(2)": None,
        "(3)": "m=0.08kg",
    }


def test_student_answer_bundle_does_not_force_freeform_solution_subquestion_slots():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"18": ["(1)", "(2)", "(3)"]},
        parts=[
            StudentAnswerPart(
                source_question_no="18",
                text=(
                    "<student>(1) T=2s，L=2.51m。"
                    "后续根据牛顿第二定律和能量守恒列式求解。</student>"
                ),
                global_confidence=0.9,
                readability_status="CLEAR",
            )
        ],
    )

    assert bundle.answers[0].slot_answers == {}
    assert "(1) T=2s" in bundle.answers[0].answer_text


def test_student_answer_bundle_creates_observed_slots_for_split_subquestion_parts():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        parts=[
            StudentAnswerPart(
                source_question_no="18/(1)",
                text="<student>T=2s</student>",
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
            StudentAnswerPart(
                source_question_no="18/(3)",
                text="<student>m=0.08kg</student>",
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
        ],
    )

    assert bundle.answers[0].slot_answers == {
        "(1)": "T=2s",
        "(3)": "m=0.08kg",
    }


def test_student_answer_bundle_maps_explicit_blank_sequence_to_fill_slots():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"2": ["blank_1", "blank_2", "blank_3", "blank_4"]},
        parts=[
            StudentAnswerPart(
                source_question_no="2",
                text=(
                    "<student>null</student>\n"
                    "<student>调幅</student>\n"
                    "<student>解调</student>\n"
                    "<student>调幅</student>"
                ),
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
        ],
    )

    assert bundle.answers[0].slot_answers == {
        "blank_1": None,
        "blank_2": "调幅",
        "blank_3": "解调",
        "blank_4": "调幅",
    }


def test_student_answer_bundle_keeps_raw_fill_blank_text_when_no_slots_align():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"2": ["blank_1", "blank_2", "blank_3", "blank_4"]},
        parts=[
            StudentAnswerPart(
                source_question_no="2",
                text=(
                    "<student>调谐</student>\n"
                    "<student>调音</student>\n"
                    "<student>解调</student>\n"
                    "<student></student>"
                ),
                elements=[
                    PerceptionNode(
                        element_id="1",
                        content_type="plain_text",
                        raw_content="2.",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.03, y_min=0.25, x_max=0.07, y_max=0.4),
                    ),
                    PerceptionNode(
                        element_id="2",
                        content_type="plain_text",
                        raw_content="<student>调谐</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.09, y_min=0.25, x_max=0.26, y_max=0.4),
                    ),
                    PerceptionNode(
                        element_id="3",
                        content_type="plain_text",
                        raw_content="<student>调音</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.38, y_min=0.25, x_max=0.55, y_max=0.4),
                    ),
                    PerceptionNode(
                        element_id="4",
                        content_type="plain_text",
                        raw_content="<student>解调</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.67, y_min=0.25, x_max=0.84, y_max=0.4),
                    ),
                    PerceptionNode(
                        element_id="5",
                        content_type="plain_text",
                        raw_content="<student></student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.96, y_min=0.25, x_max=1.0, y_max=0.4),
                    ),
                ],
                global_confidence=1.0,
                readability_status="CLEAR",
            ),
        ],
    )

    assert bundle.answers[0].slot_answers == {
        "blank_1": None,
        "blank_2": None,
        "blank_3": None,
        "blank_4": None,
    }
    assert bundle.answers[0].answer_text == "【来源题号】2\n调谐\n调音\n解调"
    assert bundle.answers[0].extraction_warnings == ["FILL_BLANK_ALIGNMENT_UNRESOLVED"]
    perception = student_answer_to_perception_output(bundle.answers[0])
    element_ids = [element.element_id for element in perception.elements]
    assert "answer_2_slot_alignment" not in element_ids


def test_student_answer_bundle_spatially_aligns_fill_blank_answers_without_shift():
    blank_y = dict(y_min=0.25, y_max=0.45)
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"2": ["blank_1", "blank_2", "blank_3", "blank_4"]},
        parts=[
            StudentAnswerPart(
                source_question_no="2",
                text=(
                    "<student>调幅</student>\n"
                    "<student>调频</student>\n"
                    "<student>调幅</student>\n"
                    "<student>调幅</student>"
                ),
                elements=[
                    PerceptionNode(
                        element_id="blank1",
                        content_type="plain_text",
                        raw_content="___________",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.08, x_max=0.26, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="blank2",
                        content_type="plain_text",
                        raw_content="___________",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.27, x_max=0.45, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="blank3",
                        content_type="plain_text",
                        raw_content="___________",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.46, x_max=0.64, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="blank4",
                        content_type="plain_text",
                        raw_content="___________",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.65, x_max=0.83, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="ans2",
                        content_type="plain_text",
                        raw_content="<student>调幅</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.29, x_max=0.41, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="ans3",
                        content_type="plain_text",
                        raw_content="<student>调频</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.48, x_max=0.60, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="ans4",
                        content_type="plain_text",
                        raw_content="<student>调幅</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.67, x_max=0.79, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="duplicate-ans2",
                        content_type="plain_text",
                        raw_content="<student>调幅</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.29, x_max=0.41, **blank_y),
                    ),
                ],
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
        ],
    )

    assert bundle.answers[0].slot_answers == {
        "blank_1": None,
        "blank_2": "调幅",
        "blank_3": "调频",
        "blank_4": "调幅",
    }
    assert bundle.answers[0].answer_text == (
        "【来源题号】2\n"
        "blank_1: 未作答\n"
        "blank_2: 调幅\n"
        "blank_3: 调频\n"
        "blank_4: 调幅"
    )
    assert bundle.answers[0].extraction_warnings == [
        "FILL_BLANK_DUPLICATE_STUDENT_MARK_IGNORED: slot=blank_2 "
        "element_id=duplicate-ans2 raw='<student>调幅</student>'"
    ]
    perception = student_answer_to_perception_output(bundle.answers[0])
    element_ids = [element.element_id for element in perception.elements]
    assert not any("duplicate-ans2" in element_id for element_id in element_ids)
    assert any("answer_2_slot_alignment" == element_id for element_id in element_ids)


def test_student_answer_to_perception_output_filters_outside_fill_blank_noise():
    blank_y = dict(y_min=0.25, y_max=0.45)
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"2": ["blank_1", "blank_2"]},
        parts=[
            StudentAnswerPart(
                source_question_no="2",
                text="<student>A</student>\n<student>A</student>",
                elements=[
                    PerceptionNode(
                        element_id="blank1",
                        content_type="plain_text",
                        raw_content="___________",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.08, x_max=0.26, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="blank2",
                        content_type="plain_text",
                        raw_content="___________",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.27, x_max=0.45, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="ans2",
                        content_type="plain_text",
                        raw_content="<student>A</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.29, x_max=0.41, **blank_y),
                    ),
                    PerceptionNode(
                        element_id="outside",
                        content_type="plain_text",
                        raw_content="<student>A</student>",
                        confidence_score=1.0,
                        bbox=BoundingBox(x_min=0.85, x_max=0.94, **blank_y),
                    ),
                ],
                global_confidence=0.9,
                readability_status="CLEAR",
            ),
        ],
    )

    assert bundle.answers[0].slot_answers == {"blank_1": None, "blank_2": "A"}
    assert bundle.answers[0].answer_text == (
        "【来源题号】2\nblank_1: 未作答\nblank_2: A"
    )
    perception = student_answer_to_perception_output(bundle.answers[0])
    raw_contents = [element.raw_content for element in perception.elements]
    assert "<student>A</student>" in raw_contents
    assert sum(1 for content in raw_contents if content == "<student>A</student>") == 1
    assert "blank_1: 未作答" in raw_contents[-1]


def test_student_answer_bundle_infers_short_left_margin_answer_without_student_tags():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        parts=[
            StudentAnswerPart(
                source_question_no="10",
                text="10. printed stem\nA. option\nB. option\nD",
                elements=[
                    PerceptionNode(
                        element_id="printed-a",
                        content_type="plain_text",
                        raw_content="A. option",
                        confidence_score=0.99,
                        bbox=BoundingBox(x_min=0.2, y_min=0.2, x_max=0.5, y_max=0.25),
                    ),
                    PerceptionNode(
                        element_id="answer-d",
                        content_type="plain_text",
                        raw_content="D",
                        confidence_score=0.9,
                        bbox=BoundingBox(x_min=0.05, y_min=0.2, x_max=0.08, y_max=0.25),
                    ),
                ],
                global_confidence=0.9,
                readability_status="CLEAR",
            )
        ],
    )

    assert bundle.answers[0].answer_text == "【来源题号】10\nD"
    assert bundle.answers[0].extraction_warnings == ["ANSWER_TEXT_INFERRED_WITHOUT_STUDENT_TAGS"]
    assert bundle.answers[0].is_blank is False


def test_student_answer_bundle_infers_worked_solution_without_student_tags():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        parts=[
            StudentAnswerPart(
                source_question_no="18",
                text=(
                    "18. (1) T = 2s\n"
                    "L = \\frac{gT^2}{4\\pi^2} = 2.51m\n"
                    "(2) m = 0.08kg\n"
                    "The right half of the image is blank with no visible content."
                ),
                global_confidence=0.9,
                readability_status="CLEAR",
            )
        ],
    )

    assert "L = \\frac{gT^2}{4\\pi^2} = 2.51m" in bundle.answers[0].answer_text
    assert "right half" not in bundle.answers[0].answer_text
    assert bundle.answers[0].extraction_warnings == [
        "ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS"
    ]
    assert bundle.answers[0].is_blank is False


def test_student_answer_bundle_drops_single_letter_noise_when_choice_tag_exists():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        parts=[
            StudentAnswerPart(
                source_question_no="10",
                text="<student>L</student>\n<student>D</student>",
                global_confidence=0.9,
                readability_status="CLEAR",
            )
        ],
    )

    assert bundle.answers[0].answer_text == "【来源题号】10\nD"


def test_student_answer_to_perception_output_preserves_original_elements():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        parts=[
            StudentAnswerPart(
                source_question_no="18",
                text="18.\nprinted stem\n<student>T=2\\pi\\sqrt{L/g}</student>",
                elements=[
                    PerceptionNode(
                        element_id="printed",
                        content_type="plain_text",
                        raw_content="18. printed stem",
                        confidence_score=0.99,
                    ),
                    PerceptionNode(
                        element_id="formula",
                        content_type="latex_formula",
                        raw_content="<student>T=2\\pi\\sqrt{L/g}</student>",
                        confidence_score=0.95,
                    ),
                ],
                global_confidence=0.95,
                readability_status="CLEAR",
            )
        ],
    )

    perception = student_answer_to_perception_output(bundle.answers[0])

    assert [element.raw_content for element in perception.elements] == [
        "18. printed stem",
        "<student>T=2\\pi\\sqrt{L/g}</student>",
    ]
    assert perception.elements[0].element_id.startswith("answer_18_part0_0_")
    assert perception.elements[1].content_type == "latex_formula"


def test_qwen_batch_prompt_keeps_fill_blank_positional_anchoring():
    prompt_path = Path("configs/prompts/qwen.perception.batch_extract.json")
    prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    system_template = prompt["variants"][0]["system_template"]

    assert prompt["meta"]["version"] == "1.1.6"
    assert "STUDENT ANSWER POSITIONAL ANCHORING" in system_template
    assert "<student>null</student>" in system_template
    assert "Never shift later student answers forward" in system_template
    assert "SUBTLE REVISION DETECTION" in system_template
    assert "Never concatenate abandoned residue with the retained answer" in system_template
    assert "调协谐" in system_template
    assert "解调振谐" in system_template
    assert "ONE BLANK, ONE WRITING LAYER" in system_template
    assert "SAME-LAYER TOKEN SELF-CHECK" in system_template
    assert "WORKED-SOLUTION BLOCK TAGGING" in system_template
    assert "you may wrap that local student-written block" in prompt["variants"][0]["user_template"]


def test_student_answer_bundle_marks_worked_solution_block_tag_and_emits_structure_hint():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        parts=[
            StudentAnswerPart(
                source_question_no="18",
                text=(
                    "18.\n"
                    "<student>(3)\n"
                    "由图可知速度不变\n"
                    "B 方向垂直纸面向里\n"
                    "mg+f=0.786\n"
                    "mg-f=0.784\n"
                    "B=0.0495T</student>"
                ),
                global_confidence=0.95,
                readability_status="CLEAR",
            )
        ],
    )

    answer = bundle.answers[0]
    assert answer.worked_solution_block_detected is True
    assert answer.answer_text == (
        "【来源题号】18\n"
        "(3)\n"
        "由图可知速度不变\n"
        "B 方向垂直纸面向里\n"
        "mg+f=0.786\n"
        "mg-f=0.784\n"
        "B=0.0495T"
    )
    perception = student_answer_to_perception_output(answer)
    structure_hints = [
        element.raw_content
        for element in perception.elements
        if element.element_id.endswith("_structure_hint")
    ]
    assert structure_hints == [
        "【结构提示】\n"
        "worked_solution_block_detected=true\n"
        "该父题内部检测到局部大段手写解答块；请保留子问标记、换行、公式链，并加强同一父题内的子问定位。"
    ]


def test_student_answer_bundle_does_not_mark_fill_blank_sequence_as_worked_solution_block():
    bundle = build_student_answer_bundle(
        paper_id="student-paper",
        expected_slots_by_question={"2": ["blank_1", "blank_2", "blank_3", "blank_4"]},
        parts=[
            StudentAnswerPart(
                source_question_no="2",
                text=(
                    "<student>调制</student>\n"
                    "<student>调幅</student>\n"
                    "<student>调谐</student>\n"
                    "<student>解调</student>"
                ),
                global_confidence=0.98,
                readability_status="CLEAR",
            )
        ],
    )

    assert bundle.answers[0].worked_solution_block_detected is False
