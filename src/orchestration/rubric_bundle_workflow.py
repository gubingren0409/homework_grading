from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from src.cognitive.base import BaseCognitiveAgent
from src.cognitive.question_tree import QuestionTreeExtractor, sort_perception_nodes
from src.orchestration.segmentation import AnswerRegionSplitter
from src.perception.base import BasePerceptionEngine
from src.perception.question_anchor import QuestionAnchorDetector
from src.schemas.perception_ir import PerceptionNode, PerceptionOutput
from src.schemas.question_ir import QuestionNumber
from src.schemas.rubric_ir import ReferenceEvidencePart, RubricBundle, TeacherRubric
from src.skills.interfaces import LayoutParseResult, LayoutRegion


class RubricBundleWorkflow:
    def __init__(
        self,
        perception_engine: BasePerceptionEngine,
        *,
        skill_service: Any | None = None,
        cognitive_agent: BaseCognitiveAgent | None = None,
        anchor_detector: QuestionAnchorDetector | None = None,
        splitter: AnswerRegionSplitter | None = None,
        question_tree_extractor: QuestionTreeExtractor | None = None,
    ) -> None:
        self._perception_engine = perception_engine
        self._skill_service = skill_service
        self._cognitive_agent = cognitive_agent
        self._anchor_detector = anchor_detector or QuestionAnchorDetector()
        self._splitter = splitter or AnswerRegionSplitter()
        self._question_tree_extractor = question_tree_extractor or QuestionTreeExtractor()

    async def generate_from_printed_reference(
        self,
        image_bytes_list: list[bytes],
        *,
        paper_id: str,
    ) -> RubricBundle:
        perception_outputs = await self._process_reference_images(image_bytes_list)
        merged_ir = self._merge_perception_outputs(perception_outputs)
        bundle = self._question_tree_extractor.extract_from_perception(merged_ir, paper_id=paper_id)
        collapsed = self._collapse_to_parent_rubrics(bundle)
        return await self._enrich_printed_rubrics(collapsed)

    async def generate_from_printed_reference_text(
        self,
        reference_text: str,
        *,
        paper_id: str,
    ) -> RubricBundle:
        bundle = self._question_tree_extractor.extract_from_markdown(reference_text, paper_id=paper_id)
        collapsed = self._collapse_to_parent_rubrics(bundle)
        return await self._enrich_printed_rubrics(collapsed)

    async def _process_reference_images(self, image_bytes_list: list[bytes]) -> list[PerceptionOutput]:
        process_images = getattr(self._perception_engine, "process_images", None)

        async def process_one(page_bytes: bytes) -> PerceptionOutput:
            if callable(process_images):
                outputs = await process_images([page_bytes], context_type="REFERENCE")
                if len(outputs) != 1:
                    raise ValueError("single-reference perception must return exactly one output")
                return outputs[0]
            return await self._perception_engine.process_image(page_bytes)

        return await asyncio.gather(*(process_one(page_bytes) for page_bytes in image_bytes_list))

    async def generate_from_handwritten_reference(
        self,
        image_bytes_list: list[bytes],
        *,
        paper_id: str,
    ) -> RubricBundle:
        perception_outputs = await self._process_reference_images(image_bytes_list)
        layout_results = await asyncio.gather(
            *[
                self._parse_layout(page_bytes, page_index=page_index)
                for page_index, page_bytes in enumerate(image_bytes_list)
            ]
        )
        perception_anchor_sets = self._anchor_detector.detect_document_from_perceptions(perception_outputs)
        anchor_sets = [
            perception_anchor_set
            if perception_anchor_set.anchors
            else self._anchor_detector.detect_from_layout(layout_result)
            for perception_anchor_set, layout_result in zip(perception_anchor_sets, layout_results)
        ]
        split_result = self._splitter.split_document(image_bytes_list, anchor_sets, layout_results)
        grouped_regions: OrderedDict[str, list[tuple[str, bytes]]] = OrderedDict()
        for region in split_result.regions:
            if not self._is_rubric_question_id(region.question_no):
                continue
            parent_question_id = self._parent_question_id(region.question_no)
            grouped_regions.setdefault(parent_question_id, []).append(
                (region.question_no, region.cropped_image_bytes)
            )
        if not grouped_regions:
            raise RuntimeError("RUBRIC_BUNDLE_NO_QUESTION_REGIONS")

        rubrics: list[TeacherRubric] = []
        for question_no, question_regions in grouped_regions.items():
            region_perceptions = await self._process_reference_images(
                [cropped_image_bytes for _, cropped_image_bytes in question_regions]
            )
            rubrics.append(
                TeacherRubric(
                    question_id=question_no,
                    correct_answer=self._format_handwritten_reference_answer(
                        question_regions,
                        region_perceptions,
                    ),
                    subquestions=self._subquestion_slots_from_question_ids(
                        source_question_id for source_question_id, _ in question_regions
                    ),
                    reference_evidence_parts=self._handwritten_reference_evidence_parts(
                        question_regions,
                        region_perceptions,
                    ),
                    solution_slots=self._handwritten_solution_slots(
                        question_regions,
                        region_perceptions,
                    ),
                )
            )

        return RubricBundle(
            paper_id=paper_id,
            rubrics=rubrics,
            question_tree=self._build_question_tree(grouped_regions.keys()),
        )

    async def _parse_layout(self, image_bytes: bytes, *, page_index: int) -> LayoutParseResult:
        if self._skill_service is not None:
            layout_result = await self._skill_service.try_parse_layout(
                image_bytes,
                context_type="REFERENCE",
                page_index=page_index,
            )
            if layout_result is not None:
                return layout_result

        extract_layout = getattr(self._perception_engine, "extract_layout", None)
        if callable(extract_layout):
            layout_ir = await extract_layout(
                image_bytes,
                context_type="REFERENCE",
                page_index=page_index,
            )
            return LayoutParseResult(
                context_type=layout_ir.context_type,
                page_index=layout_ir.page_index,
                regions=[
                    LayoutRegion(
                        target_id=region.target_id,
                        region_type=region.region_type,
                        question_no=region.question_no,
                        bbox=region.bbox.model_dump(),
                    )
                    for region in layout_ir.regions
                ],
                target_question_no=layout_ir.target_question_no,
                warnings=layout_ir.warnings,
            )

        return LayoutParseResult(
            context_type="REFERENCE",
            page_index=page_index,
            regions=[],
            warnings=["layout skill unavailable"],
        )

    def _merge_perception_outputs(self, perception_outputs: list[PerceptionOutput]) -> PerceptionOutput:
        all_elements = []
        for page_idx, output in enumerate(perception_outputs):
            for elem in output.elements:
                elem.element_id = f"p{page_idx}_{elem.element_id}"
                all_elements.append(elem)

        return PerceptionOutput(
            readability_status="CLEAR",
            elements=all_elements,
            global_confidence=(
                sum(item.global_confidence for item in perception_outputs) / len(perception_outputs)
                if perception_outputs
                else 0.0
            ),
            is_blank=all(item.is_blank for item in perception_outputs) if perception_outputs else True,
            trigger_short_circuit=False,
        )

    def _build_question_tree(self, question_ids: Any) -> list[QuestionNumber]:
        roots: list[QuestionNumber] = []
        nodes_by_path: dict[tuple[str, ...], QuestionNumber] = {}
        order_index = 0
        for question_id in question_ids:
            parts = [part for part in str(question_id).split("/") if part]
            for depth in range(1, len(parts) + 1):
                path = tuple(parts[:depth])
                if path in nodes_by_path:
                    continue
                node = QuestionNumber(
                    raw_label=parts[depth - 1],
                    normalized_path=list(path),
                    order_index=order_index,
                )
                order_index += 1
                nodes_by_path[path] = node
                if depth == 1:
                    roots.append(node)
                else:
                    nodes_by_path[tuple(parts[: depth - 1])].children.append(node)
        return roots

    def _format_handwritten_reference_answer(
        self,
        question_regions: list[tuple[str, bytes]],
        perception_outputs: list[PerceptionOutput],
    ) -> str:
        lines = ["【手写标准答案OCR】"]
        for image_index, ((source_question_id, _), perception_output) in enumerate(
            zip(question_regions, perception_outputs),
            start=1,
        ):
            text = "\n".join(
                node.raw_content.strip()
                for node in sort_perception_nodes(perception_output.elements)
                if node.raw_content.strip()
            )
            if not text:
                continue
            lines.append(f"【来源题号】{source_question_id}")
            lines.append(f"【图片{image_index}】{text}")
        return "\n".join(lines).strip()

    def _handwritten_reference_evidence_parts(
        self,
        question_regions: list[tuple[str, bytes]],
        perception_outputs: list[PerceptionOutput],
    ) -> list[ReferenceEvidencePart]:
        evidence_parts: list[ReferenceEvidencePart] = []
        for (source_question_id, _), perception_output in zip(question_regions, perception_outputs):
            text = self._perception_text(perception_output)
            if not text:
                continue
            evidence_parts.append(
                ReferenceEvidencePart(
                    source_question_no=source_question_id,
                    text=text,
                    global_confidence=perception_output.global_confidence,
                )
            )
        return evidence_parts

    def _handwritten_solution_slots(
        self,
        question_regions: list[tuple[str, bytes]],
        perception_outputs: list[PerceptionOutput],
    ) -> dict[str, str]:
        solution_slots: dict[str, str] = {}
        parent_texts: list[str] = []
        for (source_question_id, _), perception_output in zip(question_regions, perception_outputs):
            text = self._perception_text(perception_output)
            if not text:
                continue
            slot = self._subquestion_slot_from_id(source_question_id)
            if slot is None:
                parent_texts.append(text)
            else:
                solution_slots[slot] = text
        if parent_texts:
            solution_slots["parent"] = "\n".join(parent_texts).strip()
        return solution_slots

    def _perception_text(self, perception_output: PerceptionOutput) -> str:
        return "\n".join(
            node.raw_content.strip()
            for node in sort_perception_nodes(perception_output.elements)
            if node.raw_content.strip()
        )

    def _collapse_to_parent_rubrics(self, bundle: RubricBundle) -> RubricBundle:
        grouped: OrderedDict[str, list[TeacherRubric]] = OrderedDict()
        for rubric in bundle.rubrics:
            grouped.setdefault(self._parent_question_id(rubric.question_id), []).append(rubric)

        rubrics: list[TeacherRubric] = []
        for parent_question_id, source_rubrics in grouped.items():
            if len(source_rubrics) == 1 and source_rubrics[0].question_id == parent_question_id:
                rubrics.append(self._with_parent_reference_contract(source_rubrics[0]))
                continue
            answer_lines: list[str] = []
            grading_points = []
            visual_evidence = []
            for source_rubric in source_rubrics:
                answer_lines.append(f"【来源题号】{source_rubric.question_id}")
                answer_lines.append(source_rubric.correct_answer)
                grading_points.extend(source_rubric.grading_points)
                visual_evidence.extend(source_rubric.visual_evidence)
            rubrics.append(
                TeacherRubric(
                    question_id=parent_question_id,
                    correct_answer="\n".join(answer_lines).strip(),
                    grading_points=grading_points,
                    visual_evidence=visual_evidence,
                    context_stem_text=self._parent_context_text(parent_question_id, source_rubrics),
                    subquestions=self._subquestion_slots_from_question_ids(
                        rubric.question_id for rubric in source_rubrics
                    ),
                    reference_evidence_parts=[
                        ReferenceEvidencePart(
                            source_question_no=source_rubric.question_id,
                            text=source_rubric.correct_answer,
                        )
                        for source_rubric in source_rubrics
                        if source_rubric.correct_answer.strip()
                    ],
                    solution_slots=self._solution_slots_from_rubrics(parent_question_id, source_rubrics),
                )
            )

        return RubricBundle(
            paper_id=bundle.paper_id,
            rubrics=rubrics,
            question_tree=self._build_question_tree(grouped.keys()),
        )

    async def _enrich_printed_rubrics(self, bundle: RubricBundle) -> RubricBundle:
        if self._cognitive_agent is None:
            return bundle

        enriched_rubrics = await asyncio.gather(
            *[
                self._enrich_printed_rubric(rubric)
                for rubric in bundle.rubrics
            ]
        )
        return bundle.model_copy(update={"rubrics": list(enriched_rubrics)})

    async def _enrich_printed_rubric(self, rubric: TeacherRubric) -> TeacherRubric:
        perception = self._rubric_reference_perception(rubric)
        generated = await self._cognitive_agent.generate_rubric(perception)
        if not generated.grading_points:
            return rubric
        return rubric.model_copy(
            update={
                "grading_points": self._merge_grading_points(
                    existing_points=rubric.grading_points,
                    generated_points=generated.grading_points,
                ),
            }
        )

    def _merge_grading_points(
        self,
        *,
        existing_points: list[Any],
        generated_points: list[Any],
    ) -> list[Any]:
        existing_score = sum(point.score for point in existing_points)
        generated_score = sum(point.score for point in generated_points)
        if generated_score >= existing_score:
            return generated_points

        existing_by_scope = self._group_grading_points_by_scope(existing_points)
        generated_by_scope = self._group_grading_points_by_scope(generated_points)
        merged: list[Any] = []
        used_scopes: set[str] = set()
        for scope, existing_group in existing_by_scope.items():
            generated_group = generated_by_scope.get(scope, [])
            if generated_group and sum(point.score for point in generated_group) >= sum(
                point.score for point in existing_group
            ):
                merged.extend(generated_group)
            else:
                merged.extend(existing_group)
            used_scopes.add(scope)
        return merged if merged else existing_points

    def _group_grading_points_by_scope(self, points: list[Any]) -> OrderedDict[str, list[Any]]:
        grouped: OrderedDict[str, list[Any]] = OrderedDict()
        for point in points:
            scope = getattr(point, "scope", None) or "__scope_none__"
            grouped.setdefault(scope, []).append(point)
        return grouped

    def _rubric_reference_perception(self, rubric: TeacherRubric) -> PerceptionOutput:
        elements: list[PerceptionNode] = []
        for index, part in enumerate(rubric.reference_evidence_parts):
            elements.append(
                PerceptionNode(
                    element_id=f"rubric_{index}_{self._safe_element_id(part.source_question_no)}",
                    content_type="plain_text",
                    raw_content=part.text,
                    confidence_score=part.global_confidence or 1.0,
                )
            )
        if not elements and rubric.correct_answer.strip():
            elements.append(
                PerceptionNode(
                    element_id=f"rubric_{self._safe_element_id(rubric.question_id)}",
                    content_type="plain_text",
                    raw_content=rubric.correct_answer,
                    confidence_score=1.0,
                )
            )
        return PerceptionOutput(
            readability_status="CLEAR",
            elements=elements,
            global_confidence=1.0,
            is_blank=not elements,
            trigger_short_circuit=False,
        )

    def _safe_element_id(self, value: str) -> str:
        return "".join(char if char.isalnum() else "_" for char in value)

    def _with_parent_reference_contract(self, rubric: TeacherRubric) -> TeacherRubric:
        if rubric.reference_evidence_parts:
            return rubric
        return rubric.model_copy(
            update={
                "context_stem_text": rubric.context_stem_text or rubric.correct_answer,
                "reference_evidence_parts": [
                    ReferenceEvidencePart(
                        source_question_no=rubric.question_id,
                        text=rubric.correct_answer,
                    )
                ] if rubric.correct_answer.strip() else [],
                "solution_slots": rubric.solution_slots or {"parent": rubric.correct_answer},
            }
        )

    def _parent_context_text(self, parent_question_id: str, source_rubrics: list[TeacherRubric]) -> str:
        for rubric in source_rubrics:
            if rubric.question_id == parent_question_id:
                return rubric.correct_answer
        return ""

    def _solution_slots_from_rubrics(
        self,
        parent_question_id: str,
        source_rubrics: list[TeacherRubric],
    ) -> dict[str, str]:
        slots: dict[str, str] = {}
        for rubric in source_rubrics:
            slot = self._subquestion_slot_from_id(rubric.question_id)
            if slot is None:
                if rubric.question_id == parent_question_id:
                    slots["parent"] = rubric.correct_answer
                continue
            slots[slot] = rubric.correct_answer
        return slots

    def _subquestion_slots_from_question_ids(self, question_ids: Any) -> list[str]:
        slots: list[str] = []
        for question_id in question_ids:
            slot = self._subquestion_slot_from_id(str(question_id))
            if slot is not None and slot not in slots:
                slots.append(slot)
        return slots

    def _parent_question_id(self, question_id: str) -> str:
        parts = [part for part in question_id.split("/") if part]
        if len(parts) > 1 and self._is_subquestion_token(parts[-1]):
            return "/".join(parts[:-1])
        return question_id

    def _subquestion_slot_from_id(self, question_id: str) -> str | None:
        parts = [part for part in question_id.split("/") if part]
        if parts and self._is_subquestion_token(parts[-1]):
            return parts[-1]
        return None

    def _is_rubric_question_id(self, question_id: str) -> bool:
        parts = [part for part in question_id.split("/") if part]
        if not parts:
            return False
        if len(parts) > 1:
            return True
        token = parts[0]
        return (
            token.isdecimal()
            or token.startswith("(")
            or token in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
        )

    def _is_subquestion_token(self, token: str) -> bool:
        return (
            token.startswith("(")
            and token.endswith(")")
            and token[1:-1].isdigit()
        ) or token in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
