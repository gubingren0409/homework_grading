import io
import pytest
from PIL import Image, ImageDraw
from src.core.config import settings
from src.perception.factory import create_perception_engine
from src.schemas.perception_ir import PerceptionOutput


def generate_test_image_bytes() -> bytes:
    """
    In-memory generation of a simple test image containing a mathematical formula.
    """
    # Create a 400x200 white RGB image
    img = Image.new("RGB", (400, 200), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    
    # Draw a simple formula representing student handwriting
    # Note: Using default font as specific paths vary across systems
    d.text((50, 80), "y = x^2", fill=(0, 0, 0))
    
    # Save to buffer
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.skipif(
    not settings.qwen_api_key, 
    reason="Skipping real API test: QWEN_API_KEY is not configured."
)
@pytest.mark.asyncio
async def test_qwen_engine_real_network_call():
    """
    Boundary Test: Verifies that the QwenVLMPerceptionEngine can perform 
    a real network request and return a valid PerceptionOutput.
    """
    # 1. Initialization
    engine = create_perception_engine()
    image_bytes = generate_test_image_bytes()
    
    # 2. Execution
    # Calling the actual VLM (DashScope/Qwen)
    result = await engine.process_image(image_bytes)
    
    # 3. Assertions
    # Ensure the result is correctly typed
    assert isinstance(result, PerceptionOutput)
    
    # Ensure structural integrity
    assert result.readability_status in ["CLEAR", "MINOR_ALTERATION", "HEAVILY_ALTERED", "UNREADABLE"]
    assert len(result.elements) >= 0  # Even empty is valid if nothing found, but usually finds something
    
    # Confidence must be a normalized probability
    assert 0.0 <= result.global_confidence <= 1.0
    
    # Check if elements are correctly structured if they exist
    if result.elements:
        for elem in result.elements:
            assert elem.element_id is not None
            assert elem.raw_content != ""
            assert 0.0 <= elem.confidence_score <= 1.0
