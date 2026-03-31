from src.schemas.perception_ir import LayoutIR
from src.schemas.cognitive_ir import EvaluationReport, StepEvaluation


def _make_layout_with_extreme_misalignment() -> LayoutIR:
    payload = {
        "context_type": "STUDENT_ANSWER",
        "target_question_no": "13",
        "page_index": 0,
        "regions": [
            {
                "target_id": "region_A",
                "question_no": "13",
                "region_type": "answer_region",
                "bbox": {
                    "x_min": 0.0,
                    "y_min": 0.0,
                    "x_max": 0.15,
                    "y_max": 0.20,
                },
            },
            {
                "target_id": "region_B",
                "question_no": "13",
                "region_type": "answer_region",
                "bbox": {
                    "x_min": 0.84,
                    "y_min": 0.79,
                    "x_max": 1.0,
                    "y_max": 1.0,
                },
            },
        ],
        "warnings": ["extreme_misalignment_fixture"],
    }
    return LayoutIR.model_validate(payload, context={"image_width": 1800, "image_height": 2400})


def test_phase35_contract_all_regions_have_legal_bbox():
    layout = _make_layout_with_extreme_misalignment()
    assert layout.image_width == 1800
    assert layout.image_height == 2400
    assert len(layout.regions) >= 2

    for region in layout.regions:
        box = region.bbox
        assert 0.0 <= box.x_min <= 1.0
        assert 0.0 <= box.y_min <= 1.0
        assert 0.0 <= box.x_max <= 1.0
        assert 0.0 <= box.y_max <= 1.0
        assert box.x_max >= box.x_min
        assert box.y_max >= box.y_min


def test_phase35_contract_cognition_feedback_uuid_anchor_integrity():
    layout = _make_layout_with_extreme_misalignment()
    anchors = {r.target_id for r in layout.regions}

    report = EvaluationReport(
        status="SCORED",
        is_fully_correct=False,
        total_score_deduction=2.0,
        step_evaluations=[
            StepEvaluation(
                reference_element_id="region_A",
                is_correct=False,
                error_type="CALCULATION",
                correction_suggestion="符号处理错误",
            ),
            StepEvaluation(
                reference_element_id="region_B",
                is_correct=False,
                error_type="LOGIC",
                correction_suggestion="条件边界遗漏",
            ),
        ],
        overall_feedback="存在多处定位明确的扣分点",
        system_confidence=0.9,
        requires_human_review=False,
    )

    for step in report.step_evaluations:
        assert step.reference_element_id in anchors
