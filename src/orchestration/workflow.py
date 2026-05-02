import asyncio
from typing import Any, Optional

from src.cognitive.base import BaseCognitiveAgent
from src.core.exceptions import PerceptionShortCircuitError
from src.perception.base import BasePerceptionEngine
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import PerceptionOutput
from src.schemas.rubric_ir import TeacherRubric
from src.utils.file_parsers import process_multiple_files


class GradingWorkflow:
    """
    Phase34-compatible orchestrator:
    full-page perception aggregation without layout slicing preprocessing.
    """

    def __init__(
        self,
        perception_engine: BasePerceptionEngine,
        cognitive_agent: BaseCognitiveAgent,
        *,
        skill_service: Optional[Any] = None,
    ):
        del skill_service
        self._perception_engine = perception_engine
        self._cognitive_agent = cognitive_agent

    async def _evaluate_from_images(
        self,
        image_bytes_list: list[bytes],
        rubric: TeacherRubric | None = None,
    ) -> tuple[EvaluationReport, dict[str, Any], dict[str, Any]]:
        tasks = [
            self._perception_engine.process_image(page_bytes)
            for page_bytes in image_bytes_list
        ]
        perception_outputs = await asyncio.gather(*tasks)

        all_elements = []
        all_pages_blank = True

        for page_idx, perception_output in enumerate(perception_outputs):
            is_unreadable = perception_output.readability_status in ["HEAVILY_ALTERED", "UNREADABLE"]
            if perception_output.trigger_short_circuit or is_unreadable:
                raise PerceptionShortCircuitError(
                    readability_status=perception_output.readability_status,
                    message=f"Workflow halted on page {page_idx}: Image quality too poor.",
                )

            if not perception_output.is_blank:
                all_pages_blank = False

            for elem in perception_output.elements:
                elem.element_id = f"p{page_idx}_{elem.element_id}"
                all_elements.append(elem)

        if all_pages_blank:
            report = EvaluationReport(
                status="REJECTED_UNREADABLE",
                is_fully_correct=False,
                total_score_deduction=0.0,
                step_evaluations=[],
                overall_feedback="试卷未作答（检测到空白卷或无手写作答痕迹）。",
                system_confidence=1.0,
                requires_human_review=False,
            )
            perception_snapshot = {
                "readability_status": "UNREADABLE",
                "elements": [],
                "global_confidence": 0.0,
                "is_blank": True,
                "trigger_short_circuit": False,
            }
            return report, perception_snapshot, report.model_dump()

        global_conf = (
            sum(p.global_confidence for p in perception_outputs) / len(perception_outputs)
            if perception_outputs
            else 0.0
        )
        merged_ir = PerceptionOutput(
            readability_status="CLEAR",
            elements=all_elements,
            global_confidence=global_conf,
            is_blank=all_pages_blank,
            trigger_short_circuit=False,
        )

        evaluation_report = await self._cognitive_agent.evaluate_logic(merged_ir, rubric)
        if global_conf < 0.80:
            evaluation_report.requires_human_review = True

        return evaluation_report, merged_ir.model_dump(), evaluation_report.model_dump()

    async def _generate_rubric_from_images(
        self,
        image_bytes_list: list[bytes],
    ) -> TeacherRubric:
        tasks = [
            self._perception_engine.process_image(page_bytes)
            for page_bytes in image_bytes_list
        ]
        perception_outputs = await asyncio.gather(*tasks)

        all_elements = []
        for page_idx, ir_data in enumerate(perception_outputs):
            for elem in ir_data.elements:
                elem.element_id = f"p{page_idx}_{elem.element_id}"
                all_elements.append(elem)

        merged_ir = PerceptionOutput(
            readability_status="CLEAR",
            elements=all_elements,
            global_confidence=1.0,
            trigger_short_circuit=False,
        )

        rubric = await self._cognitive_agent.generate_rubric(merged_ir)
        return rubric

    async def _run_pipeline_internal(
        self,
        files_data: list[tuple[bytes, str]],
        rubric: TeacherRubric | None = None,
    ) -> tuple[EvaluationReport, dict[str, Any], dict[str, Any]]:
        image_bytes_list = await process_multiple_files(files_data)
        return await self._evaluate_from_images(image_bytes_list, rubric=rubric)

    async def run_pipeline(
        self,
        files_data: list[tuple[bytes, str]],
        rubric: TeacherRubric | None = None,
    ) -> EvaluationReport:
        report, _, _ = await self._run_pipeline_internal(files_data, rubric=rubric)
        return report

    async def run_pipeline_with_snapshots(
        self,
        files_data: list[tuple[bytes, str]],
        rubric: TeacherRubric | None = None,
    ) -> tuple[EvaluationReport, dict[str, Any], dict[str, Any]]:
        return await self._run_pipeline_internal(files_data, rubric=rubric)

    async def run_pipeline_with_preprocessed_images(
        self,
        image_bytes_list: list[bytes],
        rubric: TeacherRubric | None = None,
    ) -> tuple[EvaluationReport, dict[str, Any], dict[str, Any]]:
        return await self._evaluate_from_images(image_bytes_list, rubric=rubric)

    async def grade_question_from_preprocessed_images(
        self,
        image_bytes_list: list[bytes],
        rubric: TeacherRubric | None = None,
    ) -> EvaluationReport:
        report, _, _ = await self._evaluate_from_images(image_bytes_list, rubric=rubric)
        return report

    async def generate_rubric_pipeline(
        self,
        files_data: list[tuple[bytes, str]],
    ) -> TeacherRubric:
        image_bytes_list = await process_multiple_files(files_data)
        return await self._generate_rubric_from_images(image_bytes_list)

    async def generate_rubric_from_preprocessed_images(
        self,
        image_bytes_list: list[bytes],
    ) -> TeacherRubric:
        return await self._generate_rubric_from_images(image_bytes_list)
