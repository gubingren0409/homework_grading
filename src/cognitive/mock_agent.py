import asyncio
from typing import Any
from src.cognitive.base import BaseCognitiveAgent
from src.schemas.perception_ir import PerceptionOutput
from src.schemas.cognitive_ir import EvaluationReport, StepEvaluation


class MockCognitiveAgent(BaseCognitiveAgent):
    """
    Mock implementation of the Cognitive Agent for integration testing.
    Corrects the hardcoded calculus error from the MockPerceptionEngine.
    """

    async def evaluate_logic(
        self, 
        perception_data: PerceptionOutput,
        rubric: Any | None = None
    ) -> EvaluationReport:
        """
        Simulates reasoning latency and returns a hardcoded evaluation report.
        """
        # Simulate LLM reasoning latency
        await asyncio.sleep(0.5)

        return EvaluationReport(
            is_fully_correct=False,
            total_score_deduction=2.0,
            step_evaluations=[
                StepEvaluation(
                    reference_element_id="elem_001",
                    is_correct=False,
                    error_type="CALCULATION",
                    correction_suggestion="The integral of x^2 from 0 to 1 should be 1/3, not 1/2."
                )
            ],
            overall_feedback="Calculation error found in the integral evaluation.",
            system_confidence=1.0,
            requires_human_review=False
        )

    async def generate_rubric(self, perception_data: PerceptionOutput) -> TeacherRubric:
        """
        Simulates rubric generation for testing purposes.
        """
        from src.schemas.rubric_ir import GradingPoint
        await asyncio.sleep(0.5)
        return TeacherRubric(
            question_id="mock_q_001",
            correct_answer="The integral of x^2 from 0 to 1 is 1/3.",
            grading_points=[
                GradingPoint(point_id="p1", description="Correct integration formula", score=5.0),
                GradingPoint(point_id="p2", description="Correct bounds substitution", score=5.0)
            ]
        )
