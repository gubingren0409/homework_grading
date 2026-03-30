import asyncio
from pathlib import Path
from uuid import uuid4
from src.perception.base import BasePerceptionEngine
from src.cognitive.base import BaseCognitiveAgent
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import PerceptionOutput, LayoutIR
from src.schemas.rubric_ir import TeacherRubric
from src.core.exceptions import PerceptionShortCircuitError
from src.core.config import settings
from src.utils.file_parsers import process_multiple_files
from src.utils.image_slicer import slice_image_by_layout


class GradingWorkflow:
    """
    Central orchestrator that manages the flow between perception and cognitive layers.
    Now supports multi-page processing (PDF) and reference-based grading (Rubric).
    """

    def __init__(
        self,
        perception_engine: BasePerceptionEngine,
        cognitive_agent: BaseCognitiveAgent
    ):
        """
        Initialize the workflow with injected engine instances.
        """
        self._perception_engine = perception_engine
        self._cognitive_agent = cognitive_agent

    async def _persist_layout_slices(self, slices: dict[str, bytes], *, task_scope: str, page_idx: int) -> list[tuple[bytes, str]]:
        """
        Persist sliced images asynchronously to temporary folder and return pipeline-ready bytes.
        Uses asyncio.to_thread to avoid blocking event loop on file I/O.
        """
        target_dir = settings.uploads_path / "layout_slices" / task_scope / f"page_{page_idx}"
        await asyncio.to_thread(target_dir.mkdir, True, True)

        def _write_file(path: Path, content: bytes) -> None:
            path.write_bytes(content)

        output_files: list[tuple[bytes, str]] = []
        for target_id, content in slices.items():
            filename = f"{target_id}.png"
            out_path = target_dir / filename
            await asyncio.to_thread(_write_file, out_path, content)
            output_files.append((content, filename))
        return output_files

    async def _layout_preprocess(self, image_bytes_list: list[bytes], *, context_type: str) -> list[bytes]:
        """
        Phase 36: Optional two-stage perception preprocessor.
        1) Extract LayoutIR
        2) Slice image by layout
        3) Persist slices asynchronously
        4) Return sliced bytes (fallback to original page if slicing empty)
        """
        processed: list[bytes] = []
        task_scope = uuid4().hex
        engine = self._perception_engine

        # Soft capability check: only Qwen engine currently exposes extract_layout
        if not hasattr(engine, "extract_layout"):
            return image_bytes_list

        for page_idx, page_bytes in enumerate(image_bytes_list):
            layout: LayoutIR = await engine.extract_layout(  # type: ignore[attr-defined]
                page_bytes,
                context_type=context_type,
                page_index=page_idx,
            )
            slices = slice_image_by_layout(page_bytes, layout)
            persisted = await self._persist_layout_slices(slices, task_scope=task_scope, page_idx=page_idx)
            if persisted:
                processed.extend([b for b, _ in persisted])
            else:
                processed.append(page_bytes)
        return processed

    async def run_pipeline(
        self, 
        files_data: list[tuple[bytes, str]], 
        rubric: TeacherRubric | None = None
    ) -> EvaluationReport:
        """
        Executes the full grading pipeline for one or more student answer files.
        Aggregates multi-page IR and avoids element_id conflicts.
        Now uses asyncio.gather for parallel page perception.
        """
        # Step 1: Flatten and normalize all inputs
        image_bytes_list = await process_multiple_files(files_data)
        if settings.enable_layout_preprocess:
            image_bytes_list = await self._layout_preprocess(
                image_bytes_list,
                context_type="STUDENT_ANSWER",
            )
        
        # Step 2: Parallel Perception (Throttled by engine-level semaphore)
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
                    message=f"Workflow halted on page {page_idx}: Image quality too poor."
                )

            if not perception_output.is_blank:
                all_pages_blank = False

            # Map elements with page-specific prefix
            for elem in perception_output.elements:
                elem.element_id = f"p{page_idx}_{elem.element_id}"
                all_elements.append(elem)
        
        # Step 3: Handle Blank Page Short-circuit (Phase 26)
        if all_pages_blank:
            return EvaluationReport(
                is_fully_correct=False,
                total_score_deduction=0.0,
                step_evaluations=[],
                overall_feedback="试卷未作答（检测到空白卷或无手写作答痕迹）。",
                system_confidence=1.0,
                requires_human_review=False
            )

        # Step 4: Global Perception Aggregation
        global_conf = sum(p.global_confidence for p in perception_outputs) / len(perception_outputs) if perception_outputs else 0.0
        merged_ir = PerceptionOutput(
            readability_status="CLEAR",
            elements=all_elements,
            global_confidence=global_conf,
            is_blank=all_pages_blank,
            trigger_short_circuit=False
        )
        
        # Step 5: Cognition (Evaluate Logic against Merged IR)
        evaluation_report = await self._cognitive_agent.evaluate_logic(
            merged_ir, 
            rubric
        )

        # Task C: Defense - Force human review if perception confidence is low
        if global_conf < 0.80:
            evaluation_report.requires_human_review = True
        
        return evaluation_report

    async def generate_rubric_pipeline(
        self, 
        files_data: list[tuple[bytes, str]]
    ) -> TeacherRubric:
        """
        Orchestrates the generation of a TeacherRubric from multiple model answer files.
        Aggregates multi-page IR and avoids element_id conflicts.
        Now uses asyncio.gather for parallel page perception.
        """
        # Step 1: Flatten and normalize all inputs
        image_bytes_list = await process_multiple_files(files_data)
        if settings.enable_layout_preprocess:
            image_bytes_list = await self._layout_preprocess(
                image_bytes_list,
                context_type="REFERENCE",
            )
        
        # Step 2: Parallel Perception (Throttled by engine-level semaphore)
        tasks = [
            self._perception_engine.process_image(page_bytes)
            for page_bytes in image_bytes_list
        ]
        perception_outputs = await asyncio.gather(*tasks)
        
        all_elements = []
        for page_idx, ir_data in enumerate(perception_outputs):
            # Map elements with page-specific prefix
            for elem in ir_data.elements:
                elem.element_id = f"p{page_idx}_{elem.element_id}"
                all_elements.append(elem)
        
        # Step 3: Global Perception Aggregation
        merged_ir = PerceptionOutput(
            readability_status="CLEAR",
            elements=all_elements,
            global_confidence=1.0,
            trigger_short_circuit=False
        )
        
        # Step 4: Cognition (Distill Rubric from Merged IR)
        rubric = await self._cognitive_agent.generate_rubric(merged_ir)
        
        return rubric
