from abc import ABC, abstractmethod
from src.schemas.perception_ir import PerceptionOutput
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.rubric_ir import TeacherRubric


class BaseCognitiveAgent(ABC):
    """
    Abstract Base Class for Logical Reasoning Engines.

    Responsible for analyzing the structured IR from the perception layer
    to perform pedagogical evaluation and diagnostic grading, 
    or to generate a reference rubric from a model answer.
    """

    @abstractmethod
    async def evaluate_logic(
        self, 
        perception_data: PerceptionOutput, 
        rubric: TeacherRubric | None = None
    ) -> EvaluationReport:
        """
        Asynchronously evaluates the logic within the perception IR and returns a diagnostic report.
        """
        pass

    @abstractmethod
    async def generate_rubric(self, perception_data: PerceptionOutput) -> TeacherRubric:
        """
        Asynchronously generates a structured TeacherRubric from a model answer image (via IR).

        Args:
            perception_data: The structured IR extracted from the teacher's model answer.

        Returns:
            TeacherRubric: A structured breakdown of grading points and scores.
        """
        pass
