from __future__ import annotations

import asyncio
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, Optional

from PIL import Image

from src.cognitive.base import BaseCognitiveAgent
from src.core.config import settings
from src.orchestration.segmentation import AnswerRegionSplitter
from src.orchestration.student_answer_bundle import (
    build_student_answer_bundle,
    student_answer_to_perception_output,
)
from src.perception.base import BasePerceptionEngine
from src.perception.question_anchor import QuestionAnchorDetector
from src.schemas.answer_ir import StudentAnswerPart
from src.schemas.cognitive_ir import EvaluationReport, PaperEvaluationReport
from src.schemas.perception_ir import BoundingBox, PerceptionOutput, QuestionAnchorSet, StudentAnswerRegion
from src.schemas.rubric_ir import RubricBundle, TeacherRubric
from src.skills.interfaces import LayoutParseResult, LayoutRegion
from src.utils.file_parsers import process_multiple_files

PREVIOUS_QUESTION_BBOX_TOLERANCE = 0.04
NEXT_QUESTION_BBOX_TOLERANCE = 0.005
NUMERIC_EQUIVALENCE_REVIEW_NOTE = (
    "系统质量门禁：批改反馈中出现将等值数值 {left} 与 {right} 判为不等或不成立的矛盾，"
    "已标记人工复核，建议使用更强模型重评。"
)
_NUMERIC_TOKEN_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
_NUMERIC_CONTRADICTION_CUES = ("不等", "不相等", "不成立", "不正确", "错误", "不符")
_FILL_BLANK_RE = re.compile(r"_{2,}|＿{2,}")
_MIN_QWEN_OCR_SHORT_SIDE = 400
_NON_REVIEW_EXTRACTION_WARNING_CUES = (
    "ANSWER_TEXT_INFERRED_WITHOUT_STUDENT_TAGS",
    "ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS",
    "NO_STUDENT_TAGS_FOUND",
)


class PaperGradingWorkflow:
    def __init__(
        self,
        perception_engine: BasePerceptionEngine,
        cognitive_agent: BaseCognitiveAgent,
        *,
        skill_service: Optional[Any] = None,
        anchor_detector: Optional[QuestionAnchorDetector] = None,
        splitter: Optional[AnswerRegionSplitter] = None,
    ) -> None:
        self._perception_engine = perception_engine
        self._cognitive_agent = cognitive_agent
        self._skill_service = skill_service
        self._anchor_detector = anchor_detector or QuestionAnchorDetector()
        self._splitter = splitter or AnswerRegionSplitter()

    async def run_pipeline(
        self,
        files_data: list[tuple[bytes, str]],
        rubric_bundle: RubricBundle,
    ) -> PaperEvaluationReport:
        image_bytes_list = await process_multiple_files(files_data)
        return await self.run_pipeline_with_preprocessed_images(image_bytes_list, rubric_bundle)

    async def run_pipeline_with_preprocessed_images(
        self,
        image_bytes_list: list[bytes],
        rubric_bundle: RubricBundle,
    ) -> PaperEvaluationReport:
        perception_outputs = await self._process_images_in_chunks(
            image_bytes_list,
            context_type="student_paper_pages",
        )
        warnings = self._drain_perception_fallback_warnings()
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

        grouped_regions: dict[str, list[StudentAnswerRegion]] = {}
        for region in split_result.regions:
            grouped_regions.setdefault(region.question_no, []).append(region)

        per_question: dict[str, EvaluationReport] = {}
        warnings.extend(split_result.warnings)
        answered_questions = 0
        grading_inputs: list[tuple[TeacherRubric, list[StudentAnswerRegion]]] = []
        image_slices_by_question: dict[str, tuple[int, int]] = {}
        all_question_images: list[bytes] = []

        for rubric in rubric_bundle.rubrics:
            question_regions, fallback_question_no = self._resolve_question_regions(
                grouped_regions,
                rubric.question_id,
            )
            if not question_regions:
                per_question[rubric.question_id] = self._missing_region_report(rubric)
                warnings.append(f"question {rubric.question_id}: no answer region matched")
                continue
            if fallback_question_no is not None:
                warnings.append(
                    f"question {rubric.question_id}: using ancestor answer region {fallback_question_no}"
                )

            answered_questions += 1
            start = len(all_question_images)
            all_question_images.extend(
                self._prepare_answer_region_image(region.cropped_image_bytes)
                for region in question_regions
            )
            image_slices_by_question[rubric.question_id] = (start, len(all_question_images))
            grading_inputs.append((rubric, question_regions))

        batch_perception_outputs = await self._process_images_in_chunks(
            all_question_images,
            context_type="student_answer_regions",
        )
        warnings.extend(self._drain_perception_fallback_warnings())
        answer_parts: list[StudentAnswerPart] = []
        for rubric, question_regions in grading_inputs:
            start, end = image_slices_by_question[rubric.question_id]
            sanitized_perception_outputs = self._trim_adjacent_question_content(
                batch_perception_outputs[start:end],
                rubric.question_id,
            )
            for region, perception_output in zip(question_regions, sanitized_perception_outputs):
                answer_parts.append(
                    StudentAnswerPart(
                        source_question_no=self._aligned_region_question_no(
                            rubric.question_id,
                            region.question_no,
                        ),
                        page_index=region.page_index,
                        bbox=region.bbox,
                        text=self._perception_text(perception_output),
                        elements=perception_output.elements,
                        global_confidence=perception_output.global_confidence,
                        is_blank=perception_output.is_blank,
                        readability_status=perception_output.readability_status,
                        trigger_short_circuit=perception_output.trigger_short_circuit,
                    )
                )

        answer_bundle = build_student_answer_bundle(
            paper_id=rubric_bundle.paper_id,
            parts=answer_parts,
            expected_slots_by_question=self._expected_slots_by_question(rubric_bundle),
            stem_scope_by_question=self._stem_scope_by_question(rubric_bundle),
        )
        return await self._build_paper_report_from_answer_bundle(
            rubric_bundle=rubric_bundle,
            answer_bundle=answer_bundle,
            per_question=per_question,
            warnings=warnings,
            answered_questions=answered_questions,
            grouped_regions=grouped_regions,
        )

    async def run_pipeline_with_presegmented_images(
        self,
        image_bytes_list: list[bytes],
        rubric_bundle: RubricBundle,
        *,
        presegmented_question_ids: list[str] | None = None,
    ) -> PaperEvaluationReport:
        question_ids = presegmented_question_ids or [
            rubric.question_id for rubric in rubric_bundle.rubrics
        ]
        if len(image_bytes_list) != len(question_ids):
            raise ValueError(
                "presegmented paper grading requires one image per question id: "
                f"images={len(image_bytes_list)} question_ids={len(question_ids)}"
            )

        prepared_images = [
            self._prepare_answer_region_image(image_bytes)
            for image_bytes in image_bytes_list
        ]
        perception_outputs = await self._process_images_in_chunks(
            prepared_images,
            context_type="student_answer_regions",
        )
        warnings = self._drain_perception_fallback_warnings()
        full_bbox = BoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0)
        answer_parts = [
            StudentAnswerPart(
                source_question_no=question_id,
                page_index=index,
                bbox=full_bbox,
                text=self._perception_text(perception_output),
                elements=perception_output.elements,
                global_confidence=perception_output.global_confidence,
                is_blank=perception_output.is_blank,
                readability_status=perception_output.readability_status,
                trigger_short_circuit=perception_output.trigger_short_circuit,
            )
            for index, (question_id, perception_output) in enumerate(
                zip(question_ids, perception_outputs)
            )
        ]
        answer_bundle = build_student_answer_bundle(
            paper_id=rubric_bundle.paper_id,
            parts=answer_parts,
            expected_slots_by_question=self._expected_slots_by_question(rubric_bundle),
            stem_scope_by_question=self._stem_scope_by_question(rubric_bundle),
        )
        return await self._build_paper_report_from_answer_bundle(
            rubric_bundle=rubric_bundle,
            answer_bundle=answer_bundle,
            per_question={},
            warnings=warnings,
            answered_questions=len(answer_parts),
            grouped_regions=None,
        )

    def _expected_slots_by_question(self, rubric_bundle: RubricBundle) -> dict[str, list[str]]:
        slots_by_question: dict[str, list[str]] = {}
        for rubric in rubric_bundle.rubrics:
            question_id = self._parent_question_id(rubric.question_id)
            slots = slots_by_question.setdefault(question_id, [])
            direct_slot = self._subquestion_slot_from_id(rubric.question_id)
            if direct_slot is not None and direct_slot not in slots:
                slots.append(direct_slot)
            if not slots:
                slots.extend(self._infer_fill_blank_slots(rubric.correct_answer))
        return slots_by_question

    def _infer_fill_blank_slots(self, rubric_answer: str) -> list[str]:
        blank_count = len(_FILL_BLANK_RE.findall(rubric_answer))
        if blank_count < 2:
            return []
        return [f"blank_{index}" for index in range(1, blank_count + 1)]

    def _stem_scope_by_question(self, rubric_bundle: RubricBundle) -> dict[str, str]:
        return {
            self._parent_question_id(rubric.question_id): rubric.context_stem_text
            for rubric in rubric_bundle.rubrics
            if rubric.context_stem_text
        }

    def _aligned_region_question_no(self, rubric_question_id: str, region_question_no: str) -> str:
        if region_question_no == rubric_question_id or region_question_no.startswith(f"{rubric_question_id}/"):
            return region_question_no

        region_parts = [part for part in region_question_no.split("/") if part]
        if region_parts:
            region_slot = region_parts[-1]
            if self._is_subquestion_token(region_slot):
                return f"{rubric_question_id}/{region_slot}"

        rubric_number = self._last_numeric_question_token(rubric_question_id)
        region_number = self._last_numeric_question_token(region_question_no)
        if rubric_number is not None and rubric_number == region_number:
            return rubric_question_id
        return rubric_question_id

    async def _build_paper_report_from_answer_bundle(
        self,
        *,
        rubric_bundle: RubricBundle,
        answer_bundle: Any,
        per_question: dict[str, EvaluationReport],
        warnings: list[str],
        answered_questions: int,
        grouped_regions: dict[str, list[StudentAnswerRegion]] | None,
    ) -> PaperEvaluationReport:
        answers_by_question = {
            answer.question_id: answer
            for answer in answer_bundle.answers
        }
        pending_rubrics: list[TeacherRubric] = []
        pending_perceptions: list[PerceptionOutput] = []
        for rubric in rubric_bundle.rubrics:
            if rubric.question_id in per_question:
                continue
            answer = answers_by_question.get(rubric.question_id)
            if answer is None:
                answer = answers_by_question.get(self._parent_question_id(rubric.question_id))
            if answer is None:
                per_question[rubric.question_id] = self._missing_region_report(rubric)
                continue
            answer_perception = student_answer_to_perception_output(answer)
            if answer.extraction_warnings:
                warnings.extend(
                    f"question {rubric.question_id}: {warning}"
                    for warning in answer.extraction_warnings
                )
            pending_rubrics.append(rubric)
            pending_perceptions.append(answer_perception)

        if pending_rubrics:
            reports = await asyncio.gather(
                *[
                    self._evaluate_question_from_perceptions([answer_perception], rubric=rubric)
                    for rubric, answer_perception in zip(pending_rubrics, pending_perceptions)
                ]
            )
            for rubric, report in zip(pending_rubrics, reports):
                per_question[rubric.question_id] = report

        rubric_question_ids = {rubric.question_id for rubric in rubric_bundle.rubrics}
        if grouped_regions is not None:
            extra_questions = sorted(
                question_no
                for question_no in grouped_regions
                if not self._is_region_covered_by_rubric(question_no, rubric_question_ids)
            )
            for question_no in extra_questions:
                warnings.append(f"question {question_no}: answer region has no matching rubric")

        review_warnings = [
            warning for warning in warnings if self._warning_requires_human_review(warning)
        ]
        return PaperEvaluationReport(
            paper_id=rubric_bundle.paper_id,
            total_questions=len(rubric_bundle.rubrics),
            answered_questions=answered_questions,
            total_score_deduction=sum(report.total_score_deduction for report in per_question.values()),
            requires_human_review=bool(review_warnings) or any(
                report.requires_human_review or report.status != "SCORED"
                for report in per_question.values()
            ),
            warnings=warnings,
            per_question=per_question,
            student_answer_bundle=answer_bundle,
        )

    def _warning_requires_human_review(self, warning: str) -> bool:
        return not any(cue in warning for cue in _NON_REVIEW_EXTRACTION_WARNING_CUES)

    async def _process_images_in_chunks(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str,
    ) -> list[PerceptionOutput]:
        if not image_bytes_list:
            return []
        chunk_size, chunk_concurrency = self._image_chunk_plan(
            image_count=len(image_bytes_list),
            context_type=context_type,
        )
        chunks = [
            image_bytes_list[start : start + chunk_size]
            for start in range(0, len(image_bytes_list), chunk_size)
        ]
        if context_type == "student_answer_regions" and chunk_concurrency > 1 and len(chunks) > 1:
            return await self._process_chunks_concurrently(
                chunks,
                context_type=context_type,
                concurrency=chunk_concurrency,
            )

        outputs: list[PerceptionOutput] = []
        for chunk in chunks:
            outputs.extend(
                await self._perception_engine.process_images(
                    chunk,
                    context_type=context_type,
                )
            )
        return outputs

    def _image_chunk_plan(self, *, image_count: int, context_type: str) -> tuple[int, int]:
        configured_batch_size = max(1, int(settings.qwen_batch_max_images))
        if context_type != "student_answer_regions" or settings.qwen_answer_region_strategy == "fixed":
            concurrency = max(1, int(settings.qwen_answer_region_batch_concurrency))
            if configured_batch_size == 1:
                concurrency = max(concurrency, int(settings.qwen_single_image_concurrency))
            return configured_batch_size, min(image_count, concurrency)

        if image_count <= 1:
            return 1, 1

        api_cap = settings.effective_qwen_api_max_concurrency
        configured_cap = max(1, int(settings.qwen_answer_region_batch_concurrency))
        if configured_batch_size == 1:
            batch_size = 1
            desired_concurrency = self._desired_answer_region_concurrency(image_count)
            configured_cap = max(configured_cap, int(settings.qwen_single_image_concurrency))
        else:
            batch_size = min(2, configured_batch_size)
            desired_concurrency = self._desired_answer_region_concurrency(image_count)

        chunk_count = (image_count + batch_size - 1) // batch_size
        concurrency = min(chunk_count, desired_concurrency, configured_cap, api_cap)
        return batch_size, max(1, concurrency)

    def _desired_answer_region_concurrency(self, image_count: int) -> int:
        if image_count <= 8:
            return 2
        if image_count <= 16:
            return 4
        return 6

    async def _process_chunks_concurrently(
        self,
        chunks: list[list[bytes]],
        *,
        context_type: str,
        concurrency: int,
    ) -> list[PerceptionOutput]:
        semaphore = asyncio.Semaphore(concurrency)

        async def process_chunk(chunk: list[bytes]) -> list[PerceptionOutput]:
            async with semaphore:
                return await self._perception_engine.process_images(
                    chunk,
                    context_type=context_type,
                )

        chunk_outputs = await asyncio.gather(*(process_chunk(chunk) for chunk in chunks))
        outputs: list[PerceptionOutput] = []
        for output_group in chunk_outputs:
            outputs.extend(output_group)
        if len(outputs) != sum(len(chunk) for chunk in chunks):
            raise ValueError("chunked perception calls must return one output per input image")
        return outputs

    def _prepare_answer_region_image(self, image_bytes: bytes) -> bytes:
        with Image.open(BytesIO(image_bytes)) as image:
            image = image.convert("RGB")
            max_side = max(1, int(settings.qwen_answer_region_max_side))
            resized = False
            width, height = image.size
            if max(width, height) > max_side:
                scale = min(max_side / width, max_side / height)
                projected_width = max(1, int(width * scale))
                projected_height = max(1, int(height * scale))
                if min(projected_width, projected_height) >= _MIN_QWEN_OCR_SHORT_SIDE:
                    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                    resized = True
            buffer = BytesIO()
            if resized:
                image.save(buffer, format="JPEG", quality=82, optimize=True)
            else:
                image.save(buffer, format="PNG")
            return buffer.getvalue()

    def _resolve_question_regions(
        self,
        grouped_regions: dict[str, list[StudentAnswerRegion]],
        question_id: str,
    ) -> tuple[list[StudentAnswerRegion], str | None]:
        parent_regions = self._parent_regions(grouped_regions, question_id)
        if parent_regions:
            return parent_regions, None

        parts = question_id.split("/")
        sibling_id = self._nearest_previous_sibling(grouped_regions, parts)
        if sibling_id is not None:
            return grouped_regions[sibling_id], sibling_id

        for end in range(len(parts) - 1, 0, -1):
            ancestor_id = "/".join(parts[:end])
            ancestor = grouped_regions.get(ancestor_id, [])
            if ancestor:
                return ancestor, ancestor_id
        return [], None

    async def _evaluate_question_from_perceptions(
        self,
        perception_outputs: list[PerceptionOutput],
        *,
        rubric: TeacherRubric,
    ) -> EvaluationReport:
        if not perception_outputs:
            return self._missing_region_report(rubric)

        for page_idx, perception_output in enumerate(perception_outputs):
            is_unreadable = perception_output.readability_status in ["HEAVILY_ALTERED", "UNREADABLE"]
            if perception_output.trigger_short_circuit or is_unreadable:
                return EvaluationReport(
                    status="REJECTED_UNREADABLE",
                    is_fully_correct=False,
                    total_score_deduction=0.0,
                    step_evaluations=[],
                    overall_feedback=f"题目 {rubric.question_id} 的第 {page_idx + 1} 个作答区域无法可靠识别。",
                    system_confidence=0.0,
                    requires_human_review=True,
                )

        if all(perception_output.is_blank for perception_output in perception_outputs):
            return EvaluationReport(
                status="REJECTED_UNREADABLE",
                is_fully_correct=False,
                total_score_deduction=0.0,
                step_evaluations=[],
                overall_feedback="试卷未作答（检测到空白卷或无手写作答痕迹）。",
                system_confidence=1.0,
                requires_human_review=False,
            )

        all_elements = []
        for page_idx, perception_output in enumerate(perception_outputs):
            for elem in perception_output.elements:
                elem.element_id = f"p{page_idx}_{elem.element_id}"
                all_elements.append(elem)

        global_confidence = sum(
            perception_output.global_confidence for perception_output in perception_outputs
        ) / len(perception_outputs)
        merged_ir = PerceptionOutput(
            readability_status="CLEAR",
            elements=all_elements,
            global_confidence=global_confidence,
            is_blank=False,
            trigger_short_circuit=False,
        )

        evaluation_report = await self._cognitive_agent.evaluate_logic(merged_ir, rubric)
        if global_confidence < 0.80:
            evaluation_report.requires_human_review = True
        self._apply_numeric_equivalence_quality_gate(evaluation_report)
        return evaluation_report

    def _apply_numeric_equivalence_quality_gate(self, report: EvaluationReport) -> None:
        hit = self._find_numeric_equivalence_contradiction(report)
        if hit is None:
            return
        left, right = hit
        note = NUMERIC_EQUIVALENCE_REVIEW_NOTE.format(left=left, right=right)
        report.requires_human_review = True
        report.system_confidence = min(report.system_confidence, 0.5)
        if note not in report.overall_feedback:
            report.overall_feedback = f"{report.overall_feedback}\n{note}".strip()

    def _find_numeric_equivalence_contradiction(self, report: EvaluationReport) -> tuple[str, str] | None:
        chunks = [report.overall_feedback]
        for step in report.step_evaluations:
            if step.correction_suggestion:
                chunks.append(step.correction_suggestion)
        text = "\n".join(chunks)
        tokens = list(_NUMERIC_TOKEN_RE.finditer(text))
        for left_idx, left_match in enumerate(tokens):
            for right_match in tokens[left_idx + 1:]:
                if right_match.start() - left_match.end() > 80:
                    break
                left = left_match.group(0)
                right = right_match.group(0)
                if not self._same_value_by_trailing_zero(left, right):
                    continue
                window_start = max(0, left_match.start() - 24)
                window_end = min(len(text), right_match.end() + 24)
                window = text[window_start:window_end]
                if any(cue in window for cue in _NUMERIC_CONTRADICTION_CUES):
                    return left, right
        return None

    @staticmethod
    def _same_value_by_trailing_zero(left: str, right: str) -> bool:
        if left == right or "." not in left + right:
            return False
        try:
            if Decimal(left) != Decimal(right):
                return False
        except InvalidOperation:
            return False
        return left.rstrip("0").rstrip(".") == right.rstrip("0").rstrip(".")

    def _trim_adjacent_question_content(
        self,
        perception_outputs: list[PerceptionOutput],
        question_id: str,
    ) -> list[PerceptionOutput]:
        question_number = self._last_numeric_question_token(question_id)
        if question_number is None:
            return perception_outputs

        trimmed_outputs: list[PerceptionOutput] = []
        for perception_output in perception_outputs:
            trimmed_output = perception_output.model_copy(deep=True)
            trimmed_elements = []
            current_label_y = self._first_question_label_y(trimmed_output, question_number)
            next_label_y = self._first_next_question_label_y(trimmed_output, question_number)
            for element in trimmed_output.elements:
                has_current_label = self._question_label_pattern(question_number).search(element.raw_content) is not None
                if element.bbox is not None and not has_current_label:
                    if (
                        current_label_y is not None
                        and element.bbox.y_max < current_label_y - PREVIOUS_QUESTION_BBOX_TOLERANCE
                    ):
                        continue
                    if (
                        next_label_y is not None
                        and element.bbox.y_min >= next_label_y - NEXT_QUESTION_BBOX_TOLERANCE
                    ):
                        continue
                    raw_content = element.raw_content
                else:
                    raw_content = self._trim_text_for_question(element.raw_content, question_number)
                if not raw_content.strip():
                    continue
                element.raw_content = raw_content.strip()
                trimmed_elements.append(element)
            trimmed_output.elements = trimmed_elements
            trimmed_outputs.append(trimmed_output)
        return trimmed_outputs

    def _trim_text_for_question(self, text: str, question_number: int) -> str:
        trimmed = text
        current_match = self._question_label_pattern(question_number).search(trimmed)
        if current_match is not None:
            trimmed = trimmed[current_match.start():]

        next_matches = [
            match
            for next_question_number in range(question_number + 1, question_number + 6)
            if (match := self._question_label_pattern(next_question_number).search(trimmed)) is not None
        ]
        if next_matches:
            first_next = min(next_matches, key=lambda match: match.start())
            if first_next.start() > 0:
                trimmed = trimmed[:first_next.start()]
        return trimmed

    def _first_question_label_y(
        self,
        perception_output: PerceptionOutput,
        question_number: int,
    ) -> float | None:
        pattern = self._question_label_pattern(question_number)
        ys = [
            element.bbox.y_min
            for element in perception_output.elements
            if element.bbox is not None and pattern.search(element.raw_content)
        ]
        return min(ys) if ys else None

    def _first_next_question_label_y(
        self,
        perception_output: PerceptionOutput,
        question_number: int,
    ) -> float | None:
        ys = []
        for next_question_number in range(question_number + 1, question_number + 6):
            pattern = self._question_label_pattern(next_question_number)
            ys.extend(
                element.bbox.y_min
                for element in perception_output.elements
                if element.bbox is not None and pattern.search(element.raw_content)
            )
        return min(ys) if ys else None

    def _question_label_pattern(self, question_number: int) -> re.Pattern[str]:
        return re.compile(rf"(?m)(^|\n|\s)(?:第\s*)?{question_number}\s*(?:题|[\.．、])")

    def _last_numeric_question_token(self, question_id: str) -> int | None:
        for token in reversed(question_id.split("/")):
            if token.isdecimal():
                return int(token)
        return None

    def _perception_text(self, perception_output: PerceptionOutput) -> str:
        return "\n".join(
            element.raw_content.strip()
            for element in perception_output.elements
            if element.raw_content.strip()
        )

    def _drain_perception_fallback_warnings(self) -> list[str]:
        drain_events = getattr(self._perception_engine, "drain_batch_fallback_events", None)
        if not callable(drain_events):
            return []
        return [
            (
                "qwen batch fallback: "
                f"context={event.get('context_type')}, "
                f"image_count={event.get('image_count')}, "
                f"reason={event.get('reason')}"
            )
            for event in drain_events()
        ]

    def _parent_regions(
        self,
        grouped_regions: dict[str, list[StudentAnswerRegion]],
        question_id: str,
    ) -> list[StudentAnswerRegion]:
        regions: list[StudentAnswerRegion] = []
        prefix = f"{question_id}/"
        for region_question_no, region_items in grouped_regions.items():
            if region_question_no == question_id or region_question_no.startswith(prefix):
                regions.extend(region_items)
        return regions

    def _is_region_covered_by_rubric(
        self,
        region_question_no: str,
        rubric_question_ids: set[str],
    ) -> bool:
        if region_question_no in rubric_question_ids:
            return True
        return any(
            region_question_no.startswith(f"{rubric_question_id}/")
            for rubric_question_id in rubric_question_ids
        )

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

    def _is_subquestion_token(self, token: str) -> bool:
        return (
            token.startswith("(")
            and token.endswith(")")
            and token[1:-1].isdigit()
        ) or token in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

    def _nearest_previous_sibling(
        self,
        grouped_regions: dict[str, list[bytes]],
        parts: list[str],
    ) -> str | None:
        if len(parts) < 2:
            return None
        parent_parts = parts[:-1]
        target_order = self._question_token_order(parts[-1])
        if target_order is None:
            return None
        prefix = "/".join(parent_parts)
        best: tuple[int, str] | None = None
        for question_no in grouped_regions:
            candidate_parts = question_no.split("/")
            if candidate_parts[:-1] != parent_parts:
                continue
            candidate_order = self._question_token_order(candidate_parts[-1])
            if candidate_order is None or candidate_order >= target_order:
                continue
            if best is None or candidate_order > best[0]:
                best = (candidate_order, question_no)
        if best is None or best[1] == prefix:
            return None
        return best[1]

    def _question_token_order(self, token: str) -> int | None:
        if token.isdecimal():
            return int(token)
        if token.startswith("(") and token.endswith(")") and token[1:-1].isdigit():
            return int(token[1:-1])
        circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
        if token in circled:
            return circled.index(token) + 1
        return None

    async def _parse_layout(self, image_bytes: bytes, *, page_index: int) -> LayoutParseResult:
        if self._skill_service is not None:
            layout_result = await self._skill_service.try_parse_layout(
                image_bytes,
                context_type="STUDENT_ANSWER",
                page_index=page_index,
            )
            if layout_result is not None:
                return layout_result

        extract_layout = getattr(self._perception_engine, "extract_layout", None)
        if callable(extract_layout):
            layout_ir = await extract_layout(
                image_bytes,
                context_type="STUDENT_ANSWER",
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
            context_type="STUDENT_ANSWER",
            page_index=page_index,
            regions=[],
            warnings=["layout skill unavailable"],
        )

    def _build_anchor_set(
        self,
        *,
        perception_output: Any,
        layout_result: LayoutParseResult,
        page_index: int,
    ) -> QuestionAnchorSet:
        anchors = self._anchor_detector.detect_from_perception(perception_output, page_index=page_index)
        if anchors.anchors:
            return anchors
        return self._anchor_detector.detect_from_layout(layout_result)

    def _missing_region_report(self, rubric: TeacherRubric) -> EvaluationReport:
        return EvaluationReport(
            status="REJECTED_UNREADABLE",
            is_fully_correct=False,
            total_score_deduction=0.0,
            step_evaluations=[],
            overall_feedback=f"未找到题目 {rubric.question_id} 的对应作答区域。",
            system_confidence=0.0,
            requires_human_review=True,
        )
