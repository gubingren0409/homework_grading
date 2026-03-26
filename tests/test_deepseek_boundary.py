import pytest
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.schemas.perception_ir import PerceptionOutput, ExtractedElement
from src.schemas.cognitive_ir import EvaluationReport
from src.core.config import settings


@pytest.mark.skipif(
    not settings.deepseek_api_key, 
    reason="Skipping real API test: DEEPSEEK_API_KEY is not configured."
)
@pytest.mark.asyncio
async def test_deepseek_engine_logic_evaluation():
    """
    Boundary Test: Verifies that DeepSeekCognitiveEngine can detect a logical error 
    in a structured Perception IR and return a valid EvaluationReport.
    """
    # 1. Construct Mock Perception IR with a subtle logic error
    # Error: 2x = 4 -> x = 4 - 2 (Division replaced by Subtraction)
    mock_perception_output = PerceptionOutput(
        readability_status="CLEAR",
        elements=[
            ExtractedElement(
                element_id="step_1",
                content_type="latex_formula",
                raw_content="2x = 4",
                confidence_score=1.0
            ),
            ExtractedElement(
                element_id="step_2",
                content_type="latex_formula",
                raw_content="x = 4 - 2",  # Logical/Conceptual Error injected here
                confidence_score=1.0
            ),
            ExtractedElement(
                element_id="step_3",
                content_type="latex_formula",
                raw_content="x = 2",      # Coincidentally correct final answer, but wrong path
                confidence_score=1.0
            )
        ],
        global_confidence=0.99,
        trigger_short_circuit=False
    )

    # 2. Initialization & Execution
    engine = DeepSeekCognitiveEngine()
    report = await engine.evaluate_logic(mock_perception_output)

    # 3. Hard Assertions
    assert isinstance(report, EvaluationReport)
    
    # The overall work should be marked as incorrect due to step_2
    assert report.is_fully_correct is False
    
    # Find the evaluation for the problematic step
    step_2_eval = next(
        (eval for eval in report.step_evaluations if eval.reference_element_id == "step_2"), 
        None
    )
    
    assert step_2_eval is not None, "Evaluation for step_2 should exist"
    
    # Assert step_2 is flagged as incorrect
    assert step_2_eval.is_correct is False
    
    # Assert error attribution is within specified categories
    assert step_2_eval.error_type in ["CALCULATION", "LOGIC", "CONCEPTUAL"]
    
    # Assert meaningful feedback is provided
    assert step_2_eval.correction_suggestion is not None
    assert len(step_2_eval.correction_suggestion) > 5
    
    # Verify overall report structure
    assert 0.0 <= report.system_confidence <= 1.0
    assert report.total_score_deduction >= 0.0
