import pytest
from pydantic import ValidationError
from src.schemas.cognitive_ir import EvaluationReport


def test_evaluation_report_strict_validation():
    """
    Asserts that EvaluationReport correctly enforces boundary constraints and 
    Literal type checks on incoming data payloads.
    """
    # Negative Payload with three deliberate violations:
    # 1. total_score_deduction < 0.0 (Constraint ge=0.0)
    # 2. error_type is an illegal string (Constraint Literal)
    # 3. system_confidence > 1.0 (Constraint le=1.0)
    invalid_payload = {
        "is_fully_correct": False,
        "total_score_deduction": -5.0,
        "step_evaluations": [
            {
                "reference_element_id": "elem_001",
                "is_correct": False,
                "error_type": "MATH_CALC_ERROR",
                "correction_suggestion": "The calculation is fundamentally wrong."
            }
        ],
        "overall_feedback": "The integral result is incorrect.",
        "system_confidence": 1.5,
        "requires_human_review": True
    }

    with pytest.raises(ValidationError) as exc_info:
        EvaluationReport(**invalid_payload)

    # Convert errors to list of dicts for easier inspection
    errors = exc_info.value.errors()
    error_fields = [e["loc"][0] for e in errors]
    
    # Assert all three illegal fields were correctly intercepted
    assert "total_score_deduction" in error_fields
    # For nested error_type, it might be in loc list like ('step_evaluations', 0, 'error_type')
    nested_error_fields = [e["loc"] for e in errors]
    assert any("error_type" in loc for loc in nested_error_fields)
    assert "system_confidence" in error_fields
