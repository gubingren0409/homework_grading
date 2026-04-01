import asyncio
from io import BytesIO

from PIL import Image

from src.perception.base import BasePerceptionEngine
from src.schemas.perception_ir import PerceptionOutput, PerceptionNode, BoundingBox, LayoutIR


class MockPerceptionEngine(BasePerceptionEngine):
    """
    Mock implementation of the Perception Engine for integration testing.
    Returns a deterministic IR containing a calculus error.
    """

    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        """
        Simulates image processing with a fixed delay and returns hardcoded IR.
        """
        # Simulate network/processing latency
        await asyncio.sleep(0.5)

        return PerceptionOutput(
            readability_status="CLEAR",
            elements=[
                PerceptionNode(
                    element_id="elem_001",
                    content_type="latex_formula",
                    raw_content="\\int_{0}^{1} x^2 dx = \\frac{1}{2}",
                    confidence_score=0.99,
                    bbox=BoundingBox(
                        x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.2
                    )
                )
            ],
            global_confidence=0.98,
            trigger_short_circuit=False
        )

    async def extract_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        target_question_no: str | None = None,
        page_index: int = 0,
    ) -> LayoutIR:
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size

        payload = {
            "context_type": context_type,
            "target_question_no": target_question_no,
            "page_index": page_index,
            "regions": [
                {
                    "target_id": f"region_{page_index}_0",
                    "question_no": target_question_no,
                    "region_type": "answer_region",
                    "bbox": {
                        "x_min": 0.0,
                        "y_min": 0.0,
                        "x_max": 1.0,
                        "y_max": 1.0,
                    },
                }
            ],
            "warnings": [],
        }
        return LayoutIR.model_validate(payload, context={"image_width": width, "image_height": height})
