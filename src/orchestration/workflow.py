import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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


@dataclass(frozen=True)
class _SliceLayoutBinding:
    page_index: int
    region_id: str
    region_type: str
    bbox: dict[str, float]
    image_width: int
    image_height: int


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

    def _enforce_phase35_contract_enabled(self) -> None:
        if not settings.enable_layout_preprocess:
            raise RuntimeError(
                "PHASE35_CONTRACT_BLOCK: enable_layout_preprocess must remain enabled."
            )

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

    async def _layout_preprocess(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str,
    ) -> tuple[list[bytes], list[_SliceLayoutBinding], list[LayoutIR]]:
        """
        Phase 36: Mandatory two-stage perception preprocessor.
        1) Extract LayoutIR
        2) Slice image by layout
        3) Persist slices asynchronously
        4) Return sliced bytes with strict layout bindings
        """
        processed: list[bytes] = []
        bindings: list[_SliceLayoutBinding] = []
        layouts: list[LayoutIR] = []
        task_scope = uuid4().hex
        engine = self._perception_engine

        # Hard capability gate: no layout capability means hard block.
        if not hasattr(engine, "extract_layout"):
            raise RuntimeError(
                "PHASE35_CONTRACT_BLOCK: perception engine lacks extract_layout capability."
            )

        for page_idx, page_bytes in enumerate(image_bytes_list):
            layout: LayoutIR = await engine.extract_layout(  # type: ignore[attr-defined]
                page_bytes,
                context_type=context_type,
                page_index=page_idx,
            )
            image_width = int(layout.image_width or 0)
            image_height = int(layout.image_height or 0)
            if image_width <= 0 or image_height <= 0:
                raise RuntimeError(
                    f"PHASE35_CONTRACT_BLOCK: invalid layout dimensions on page {page_idx}."
                )
            if not layout.regions:
                raise RuntimeError(
                    f"PHASE35_CONTRACT_BLOCK: layout regions empty on page {page_idx}."
                )

            slices = slice_image_by_layout(page_bytes, layout)
            if not slices:
                raise RuntimeError(
                    f"PHASE35_CONTRACT_BLOCK: slicing produced no regions on page {page_idx}."
                )

            persisted = await self._persist_layout_slices(
                slices,
                task_scope=task_scope,
                page_idx=page_idx,
            )
            persisted_by_name = {name.rsplit(".", 1)[0]: content for content, name in persisted}

            for region in layout.regions:
                crop_bytes = persisted_by_name.get(region.target_id)
                if crop_bytes is None:
                    raise RuntimeError(
                        "PHASE35_CONTRACT_BLOCK: missing sliced crop for region "
                        f"{region.target_id} on page {page_idx}."
                    )
                canonical_region_id = f"p{page_idx}_{region.target_id}"
                processed.append(crop_bytes)
                bindings.append(
                    _SliceLayoutBinding(
                        page_index=page_idx,
                        region_id=canonical_region_id,
                        region_type=region.region_type,
                        bbox=region.bbox.model_dump(),
                        image_width=image_width,
                        image_height=image_height,
                    )
                )
            layouts.append(layout)

        return processed, bindings, layouts

    def _build_snapshot_payloads(
        self,
        *,
        evaluation_report: EvaluationReport,
        layout_bindings: list[_SliceLayoutBinding],
        element_to_region: dict[str, str],
        legacy_element_to_region: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        unique_regions: dict[str, _SliceLayoutBinding] = {}
        for binding in layout_bindings:
            unique_regions[binding.region_id] = binding
        if not unique_regions:
            raise RuntimeError("PHASE35_CONTRACT_BLOCK: no layout regions available for snapshot persistence.")

        perception_snapshot = {
            "context_type": "STUDENT_ANSWER",
            "regions": [
                {
                    "target_id": binding.region_id,
                    "question_no": None,
                    "region_type": binding.region_type,
                    "bbox": binding.bbox,
                    "page_index": binding.page_index,
                    "image_width": binding.image_width,
                    "image_height": binding.image_height,
                }
                for binding in unique_regions.values()
            ],
            "warnings": [],
        }

        cognitive_snapshot = evaluation_report.model_dump()
        steps = cognitive_snapshot.get("step_evaluations", [])
        if not isinstance(steps, list):
            raise RuntimeError(
                "PHASE35_CONTRACT_BLOCK: cognitive snapshot step_evaluations malformed."
            )

        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                raise RuntimeError(
                    f"PHASE35_CONTRACT_BLOCK: cognitive step[{idx}] is not an object."
                )
            ref = str(step.get("reference_element_id") or "")
            if not ref:
                raise RuntimeError(
                    f"PHASE35_CONTRACT_BLOCK: cognitive step[{idx}] missing reference_element_id."
                )
            region_id = element_to_region.get(ref)
            if region_id is None:
                region_id = legacy_element_to_region.get(ref)
            if region_id is None:
                raise RuntimeError(
                    "PHASE35_CONTRACT_BLOCK: cognitive reference cannot be mapped to a layout region: "
                    f"{ref}."
                )
            step["reference_element_id"] = region_id

        return perception_snapshot, cognitive_snapshot

    async def _run_pipeline_internal(
        self,
        files_data: list[tuple[bytes, str]],
        rubric: TeacherRubric | None = None,
    ) -> tuple[EvaluationReport, dict[str, Any], dict[str, Any]]:
        """
        Internal execution path returning grading report plus persisted snapshots.
        """
        image_bytes_list = await process_multiple_files(files_data)
        self._enforce_phase35_contract_enabled()
        image_bytes_list, slice_bindings, _ = await self._layout_preprocess(
            image_bytes_list,
            context_type="STUDENT_ANSWER",
        )

        tasks = [
            self._perception_engine.process_image(page_bytes)
            for page_bytes in image_bytes_list
        ]
        perception_outputs = await asyncio.gather(*tasks)

        all_elements = []
        all_pages_blank = True
        element_to_region: dict[str, str] = {}
        legacy_element_to_region: dict[str, str] = {}

        for slice_idx, perception_output in enumerate(perception_outputs):
            is_unreadable = perception_output.readability_status in ["HEAVILY_ALTERED", "UNREADABLE"]
            if perception_output.trigger_short_circuit or is_unreadable:
                raise PerceptionShortCircuitError(
                    readability_status=perception_output.readability_status,
                    message=f"Workflow halted on page {slice_idx}: Image quality too poor."
                )

            if not perception_output.is_blank:
                all_pages_blank = False

            binding = slice_bindings[slice_idx]
            for elem in perception_output.elements:
                original_id = elem.element_id
                elem.element_id = f"{binding.region_id}::{original_id}"
                all_elements.append(elem)
                element_to_region[elem.element_id] = binding.region_id
                legacy_element_to_region.setdefault(original_id, binding.region_id)

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
            perception_snapshot, cognitive_snapshot = self._build_snapshot_payloads(
                evaluation_report=report,
                layout_bindings=slice_bindings,
                element_to_region=element_to_region,
                legacy_element_to_region=legacy_element_to_region,
            )
            return report, perception_snapshot, cognitive_snapshot

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

        perception_snapshot, cognitive_snapshot = self._build_snapshot_payloads(
            evaluation_report=evaluation_report,
            layout_bindings=slice_bindings,
            element_to_region=element_to_region,
            legacy_element_to_region=legacy_element_to_region,
        )
        return evaluation_report, perception_snapshot, cognitive_snapshot

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
        report, _, _ = await self._run_pipeline_internal(files_data, rubric=rubric)
        return report

    async def run_pipeline_with_snapshots(
        self,
        files_data: list[tuple[bytes, str]],
        rubric: TeacherRubric | None = None,
    ) -> tuple[EvaluationReport, dict[str, Any], dict[str, Any]]:
        """
        Execute grading pipeline and return report + persisted snapshot payloads.
        """
        return await self._run_pipeline_internal(files_data, rubric=rubric)

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
        self._enforce_phase35_contract_enabled()
        image_bytes_list, _, _ = await self._layout_preprocess(
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
