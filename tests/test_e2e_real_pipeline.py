import io
import pytest
from PIL import Image, ImageDraw
from src.core.config import settings
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.schemas.cognitive_ir import EvaluationReport


def generate_e2e_test_image_bytes() -> bytes:
    """
    Dynamically generates a test image with a calculus error for E2E testing.
    Content: lim(x->0) sin(x)/x = 0 (The correct limit should be 1)
    """
    img = Image.new("RGB", (600, 300), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    
    # Text simulating a student's incorrect solution
    d.text((50, 50), "Calculus Homework", fill=(0, 0, 0))
    d.text((50, 120), "Problem 1: Calculate lim(x->0) sin(x)/x", fill=(0, 0, 0))
    d.text((50, 180), "Solution: lim(x->0) sin(x)/x = 0", fill=(0, 0, 0)) # Incorrect
    
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.skipif(
    not (settings.qwen_api_key and settings.deepseek_api_key),
    reason="Skipping E2E Pipeline test: QWEN_API_KEY or DEEPSEEK_API_KEY is missing."
)
@pytest.mark.asyncio
async def test_full_pipeline_with_real_engines():
    """
    Phase 5 E2E Test: Validates the full physical chain from Image -> VLM -> LLM -> Report.
    Focuses on schema integrity and system connectivity.
    """
    # 1. Setup real infrastructure
    perception_engine = QwenVLMPerceptionEngine()
    cognitive_agent = DeepSeekCognitiveEngine()
    
    # 2. Orchestration
    workflow = GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent
    )
    
    # 3. Execution
    image_bytes = generate_e2e_test_image_bytes()
    report = await workflow.run_pipeline([(image_bytes, "e2e.jpg")])
    
    # 4. Resilient Assertions
    # Ensure it's the correct type
    assert isinstance(report, EvaluationReport)
    
    # Ensure logical flow happened (VLM extracted and LLM evaluated)
    assert len(report.step_evaluations) > 0, "Pipeline should extract at least one element and evaluate it."
    
    # Ensure metadata integrity
    assert 0.0 <= report.system_confidence <= 1.0
    assert isinstance(report.is_fully_correct, bool)
    assert isinstance(report.overall_feedback, str)
    assert len(report.overall_feedback) > 0
    
    # Confirm structural connectivity
    # Step evaluations should reference something that existed in the VLM output
    for step in report.step_evaluations:
        assert step.reference_element_id is not None
        assert isinstance(step.is_correct, bool)
