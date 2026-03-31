import asyncio
from src.perception.base import BasePerceptionEngine
from src.schemas.perception_ir import PerceptionOutput, PerceptionNode, BoundingBox


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
