from abc import ABC, abstractmethod
import asyncio

from src.schemas.perception_ir import PerceptionOutput


class BasePerceptionEngine(ABC):
    """
    Abstract Base Class for Visual Perception Engines.

    Responsible for extracting structural elements from raw image data and
    converting them into a standardized PerceptionOutput Intermediate Representation (IR).

    Constraints:
    - Must be agnostic to the underlying VLM/OCR provider (OpenAI, DashScope, etc.).
    - Strictly prohibited from performing logical reasoning or pedagogical grading.
    """

    @abstractmethod
    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        """
        Asynchronously processes raw image bytes and returns a structured IR.

        Args:
            image_bytes: The raw binary content of the homework image.

        Returns:
            PerceptionOutput: Standardized intermediate representation of the image content.
        """
        pass

    async def process_images(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str = "student_homework",
    ) -> list[PerceptionOutput]:
        """
        Batch perception hook. Engines that support multi-image VLM calls can override this
        to reduce model invocations; the default preserves existing behavior.
        """
        del context_type
        return await asyncio.gather(
            *[self.process_image(image_bytes) for image_bytes in image_bytes_list]
        )
