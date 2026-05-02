from __future__ import annotations

import re
from collections import OrderedDict
from typing import Iterable, Mapping

from src.schemas.answer_ir import StudentAnswer, StudentAnswerBundle, StudentAnswerPart
from src.schemas.perception_ir import PerceptionNode, PerceptionOutput
from src.schemas.question_ir import QuestionNumber

_READABILITY_PRIORITY = {
    "CLEAR": 0,
    "MINOR_ALTERATION": 1,
    "HEAVILY_ALTERED": 2,
    "UNREADABLE": 3,
}


def build_student_answer_bundle(
    *,
    paper_id: str,
    parts: Iterable[StudentAnswerPart],
    expected_slots_by_question: Mapping[str, list[str]] | None = None,
    stem_scope_by_question: Mapping[str, str] | None = None,
) -> StudentAnswerBundle:
    grouped: OrderedDict[str, list[StudentAnswerPart]] = OrderedDict()
    for part in parts:
        grouped.setdefault(_parent_question_id(part.source_question_no), []).append(part)

    answers: list[StudentAnswer] = []
    for question_id, grouped_parts in grouped.items():
        normalized_parts = [_with_extracted_answer_text(part) for part in grouped_parts]
        answer_text = _merge_answer_text(normalized_parts)
        ocr_text = _merge_ocr_text(normalized_parts)
        expected_slots = (
            list((expected_slots_by_question or {}).get(question_id, []))
            if expected_slots_by_question is not None
            else []
        )
        slot_answers, slot_warnings = _build_slot_answers(normalized_parts, expected_slots)
        if _should_render_fill_blank_slot_answers(slot_answers):
            answer_text = _render_slot_answer_text(question_id, slot_answers)
        confidence_values = [part.global_confidence for part in grouped_parts]
        worst_readability_status = max(
            (part.readability_status for part in grouped_parts),
            key=lambda status: _READABILITY_PRIORITY.get(status, 0),
            default="CLEAR",
        )
        extraction_warnings = [
            warning
            for part in normalized_parts
            for warning in part.extraction_warnings
        ]
        extraction_warnings.extend(slot_warnings)
        answers.append(
            StudentAnswer(
                question_id=question_id,
                stem_scope=(stem_scope_by_question or {}).get(question_id, ""),
                answer_text=answer_text,
                ocr_text=ocr_text,
                parts=normalized_parts,
                answer_parts=normalized_parts,
                slot_answers=slot_answers,
                global_confidence=(
                    sum(confidence_values) / len(confidence_values)
                    if confidence_values
                    else 0.0
                ),
                is_blank=all(part.is_blank for part in normalized_parts) or not answer_text,
                readability_status=worst_readability_status,
                trigger_short_circuit=any(part.trigger_short_circuit for part in normalized_parts),
                extraction_warnings=extraction_warnings,
                worked_solution_block_detected=any(
                    part.worked_solution_block_detected for part in normalized_parts
                ),
            )
        )

    return StudentAnswerBundle(
        paper_id=paper_id,
        answers=answers,
        question_tree=_build_question_tree(grouped.keys()),
    )


def _build_slot_answers(
    parts: list[StudentAnswerPart],
    expected_slots: list[str],
) -> tuple[dict[str, str | None], list[str]]:
    slot_answers: OrderedDict[str, str | None] = OrderedDict(
        (slot, None) for slot in expected_slots
    )
    warnings: list[str] = []
    if expected_slots and all(_is_subquestion_token(slot) for slot in expected_slots):
        saw_direct_subquestion_part = False
        for part in parts:
            direct_slot = _subquestion_slot_from_id(part.source_question_no)
            if direct_slot is None:
                continue
            saw_direct_subquestion_part = True
            _merge_slot_answer(slot_answers, direct_slot, part.answer_text)
        if saw_direct_subquestion_part:
            return dict(slot_answers), warnings
        return {}, warnings

    for part in parts:
        direct_slot = _subquestion_slot_from_id(part.source_question_no)
        if direct_slot is not None:
            if not expected_slots and direct_slot not in slot_answers:
                slot_answers[direct_slot] = None
            _merge_slot_answer(slot_answers, direct_slot, part.answer_text)
            continue
        spatial_segments, spatial_warnings = _extract_spatial_fill_blank_answers(part, expected_slots)
        warnings.extend(spatial_warnings)
        if spatial_segments:
            for slot, answer in spatial_segments.items():
                _merge_slot_answer(slot_answers, slot, answer)
            continue
        labelled_segments = _extract_slot_segments(part.answer_text, expected_slots)
        for slot, answer in labelled_segments.items():
            _merge_slot_answer(slot_answers, slot, answer)
        if not labelled_segments:
            for slot, answer in _extract_sequential_slot_answers(
                part.answer_text,
                expected_slots,
            ).items():
                _merge_slot_answer(slot_answers, slot, answer)
    if (
        expected_slots
        and all(slot.startswith("blank_") for slot in expected_slots)
        and not any(answer is not None for answer in slot_answers.values())
        and any(part.answer_text.strip() for part in parts)
    ):
        warnings.append("FILL_BLANK_ALIGNMENT_UNRESOLVED")
    return dict(slot_answers), warnings


def _render_slot_answer_text(question_id: str, slot_answers: dict[str, str | None]) -> str:
    lines = [f"【来源题号】{question_id}"]
    for slot, answer in slot_answers.items():
        lines.append(f"{slot}: {'未作答' if answer is None else answer}")
    return "\n".join(lines)


def _should_render_fill_blank_slot_answers(slot_answers: Mapping[str, str | None]) -> bool:
    return bool(slot_answers) and all(slot.startswith("blank_") for slot in slot_answers) and any(
        answer is not None for answer in slot_answers.values()
    )


def _extract_spatial_fill_blank_answers(
    part: StudentAnswerPart,
    expected_slots: list[str],
) -> tuple[dict[str, str | None], list[str]]:
    if not expected_slots or not all(slot.startswith("blank_") for slot in expected_slots):
        return {}, []
    warnings: list[str] = []
    blank_elements = [
        element
        for element in part.elements
        if element.bbox is not None and _looks_like_blank_placeholder(element.raw_content)
    ]
    student_elements = [
        element
        for element in part.elements
        if element.bbox is not None and _extract_student_tag_text(element.raw_content)
    ]
    if len(blank_elements) < 2 or not student_elements:
        return {}, []

    blank_elements = sorted(
        blank_elements,
        key=lambda element: (element.bbox.y_min, element.bbox.x_min),  # type: ignore[union-attr]
    )
    if len(blank_elements) != len(expected_slots):
        warnings.append(
            "FILL_BLANK_SLOT_COUNT_MISMATCH: "
            f"expected_slots={len(expected_slots)} detected_blanks={len(blank_elements)}"
        )
    slots = expected_slots[: len(blank_elements)]
    segments: dict[str, str | None] = {slot: None for slot in slots}
    seen_by_slot: dict[str, set[str]] = {slot: set() for slot in slots}
    for student_element in student_elements:
        target_index = _containing_blank_index(student_element, blank_elements)
        if target_index is None or target_index >= len(slots):
            warnings.append(
                "FILL_BLANK_STUDENT_MARK_OUTSIDE_SLOT: "
                f"element_id={student_element.element_id} raw={student_element.raw_content!r}"
            )
            continue
        slot = slots[target_index]
        answer = _extract_student_tag_text(student_element.raw_content)
        if not answer:
            continue
        normalized = _normalize_slot_answer(answer)
        dedupe_key = "" if normalized is None else re.sub(r"\s+", "", normalized)
        if dedupe_key in seen_by_slot[slot]:
            warnings.append(
                "FILL_BLANK_DUPLICATE_STUDENT_MARK_IGNORED: "
                f"slot={slot} element_id={student_element.element_id} raw={student_element.raw_content!r}"
            )
            continue
        seen_by_slot[slot].add(dedupe_key)
        if normalized is None:
            continue
        existing = segments[slot]
        if existing is not None and existing != normalized:
            warnings.append(
                "FILL_BLANK_MULTIPLE_DISTINCT_MARKS_IN_SLOT: "
                f"slot={slot} existing={existing!r} new={normalized!r}"
            )
        segments[slot] = normalized if existing is None else f"{existing}\n{normalized}"
    return segments, warnings


def _looks_like_blank_placeholder(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return re.fullmatch(r"[_＿—-]{3,}", compact) is not None


def _containing_blank_index(
    student_element: PerceptionNode,
    blank_elements: list[PerceptionNode],
) -> int | None:
    if student_element.bbox is None:
        return None
    student_center_x = (student_element.bbox.x_min + student_element.bbox.x_max) / 2
    candidates: list[tuple[float, int]] = []
    for index, blank_element in enumerate(blank_elements):
        if blank_element.bbox is None:
            continue
        y_overlap = (
            student_element.bbox.y_min <= blank_element.bbox.y_max + 0.04
            and student_element.bbox.y_max >= blank_element.bbox.y_min - 0.04
        )
        if not y_overlap:
            continue
        if not (blank_element.bbox.x_min - 0.03 <= student_center_x <= blank_element.bbox.x_max + 0.03):
            continue
        blank_center_x = (blank_element.bbox.x_min + blank_element.bbox.x_max) / 2
        candidates.append((abs(student_center_x - blank_center_x), index))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _merge_slot_answer(
    slot_answers: OrderedDict[str, str | None],
    slot: str,
    answer_text: str | None,
) -> None:
    if answer_text is None:
        slot_answers.setdefault(slot, None)
        return
    normalized = _normalize_slot_answer(answer_text)
    if slot not in slot_answers:
        slot_answers[slot] = normalized
        return
    if normalized is None:
        return
    existing = slot_answers[slot]
    slot_answers[slot] = normalized if existing is None else f"{existing}\n{normalized}"


def _normalize_slot_answer(answer_text: str) -> str | None:
    answer = answer_text.strip()
    if not answer or re.fullmatch(r"(?i)null|none|n/a|未作答|空白|无", answer):
        return None
    return answer


def _extract_slot_segments(text: str, expected_slots: list[str]) -> dict[str, str]:
    if not text.strip() or not expected_slots:
        return {}
    slot_pattern = "|".join(re.escape(slot) for slot in expected_slots)
    matches = list(re.finditer(rf"(?P<slot>{slot_pattern})", text))
    segments: dict[str, str] = {}
    for index, match in enumerate(matches):
        slot = match.group("slot")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end():end].strip(" \n:：，,。")
        if segment:
            segments[slot] = segment
    return segments


def _extract_sequential_slot_answers(text: str, expected_slots: list[str]) -> dict[str, str | None]:
    tokens = _answer_tokens_without_source_markers(text)
    if not tokens or not expected_slots:
        return {}
    has_explicit_missing = any(_normalize_slot_answer(token) is None for token in tokens)
    if len(tokens) != len(expected_slots) and not has_explicit_missing:
        return {}

    segments: dict[str, str | None] = {}
    for slot, token in zip(expected_slots, tokens):
        segments[slot] = token
    return segments


def _answer_tokens_without_source_markers(text: str) -> list[str]:
    tokens: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("【来源题号】"):
            continue
        tokens.append(line)
    return tokens


def student_answer_to_perception_output(answer: StudentAnswer) -> PerceptionOutput:
    elements = []
    ignored_fill_blank_elements = _ignored_fill_blank_student_element_ids(answer)
    for part_index, part in enumerate(answer.parts):
        for element_index, element in enumerate(part.elements):
            if element.element_id in ignored_fill_blank_elements.get(part_index, set()):
                continue
            elements.append(
                element.model_copy(
                    update={
                        "element_id": (
                            f"answer_{answer.question_id}_"
                            f"part{part_index}_{element_index}_{element.element_id}"
                        )
                    }
                )
            )
    if answer.slot_answers and (
        not all(slot.startswith("blank_") for slot in answer.slot_answers)
        or any(value is not None for value in answer.slot_answers.values())
    ):
        slot_lines = [
            f"{slot}: {'未作答' if value is None else value}"
            for slot, value in answer.slot_answers.items()
        ]
        elements.append(
            PerceptionNode(
                element_id=f"answer_{answer.question_id}_slot_alignment",
                content_type="plain_text",
                raw_content="【结构化作答槽位】\n" + "\n".join(slot_lines),
                confidence_score=answer.global_confidence,
            )
        )
    if answer.worked_solution_block_detected:
        elements.append(
            PerceptionNode(
                element_id=f"answer_{answer.question_id}_structure_hint",
                content_type="plain_text",
                raw_content=(
                    "【结构提示】\n"
                    "worked_solution_block_detected=true\n"
                    "该父题内部检测到局部大段手写解答块；请保留子问标记、换行、公式链，并加强同一父题内的子问定位。"
                ),
                confidence_score=answer.global_confidence,
            )
        )
    if answer.answer_text.strip():
        if not elements:
            elements.append(
                PerceptionNode(
                    element_id=f"answer_{answer.question_id}",
                    content_type="plain_text",
                    raw_content=answer.answer_text,
                    confidence_score=answer.global_confidence,
                )
            )
    return PerceptionOutput(
        readability_status=answer.readability_status,
        elements=elements,
        global_confidence=answer.global_confidence,
        is_blank=answer.is_blank or not elements,
        trigger_short_circuit=answer.trigger_short_circuit,
    )


def _ignored_fill_blank_student_element_ids(answer: StudentAnswer) -> dict[int, set[str]]:
    if not answer.slot_answers or not all(
        slot.startswith("blank_") for slot in answer.slot_answers
    ):
        return {}
    slots = list(answer.slot_answers)
    ignored_by_part: dict[int, set[str]] = {}
    for part_index, part in enumerate(answer.parts):
        blank_elements = sorted(
            [
                element
                for element in part.elements
                if element.bbox is not None and _looks_like_blank_placeholder(element.raw_content)
            ],
            key=lambda element: (element.bbox.y_min, element.bbox.x_min),  # type: ignore[union-attr]
        )
        if not blank_elements:
            continue
        seen_by_slot: dict[str, set[str]] = {slot: set() for slot in slots}
        ignored: set[str] = set()
        for element in part.elements:
            answer_text = _extract_student_tag_text(element.raw_content)
            if element.bbox is None or answer_text is None:
                continue
            target_index = _containing_blank_index(element, blank_elements)
            if target_index is None or target_index >= len(slots):
                ignored.add(element.element_id)
                continue
            slot = slots[target_index]
            normalized = _normalize_slot_answer(answer_text)
            dedupe_key = "" if normalized is None else re.sub(r"\s+", "", normalized)
            if dedupe_key in seen_by_slot[slot]:
                ignored.add(element.element_id)
                continue
            seen_by_slot[slot].add(dedupe_key)
        if ignored:
            ignored_by_part[part_index] = ignored
    return ignored_by_part


def _merge_answer_text(parts: list[StudentAnswerPart]) -> str:
    lines: list[str] = []
    for part in parts:
        if not part.answer_text.strip():
            continue
        lines.append(f"【来源题号】{part.source_question_no}")
        lines.append(part.answer_text.strip())
    return "\n".join(lines).strip()


def _merge_ocr_text(parts: list[StudentAnswerPart]) -> str:
    lines: list[str] = []
    for part in parts:
        if not part.text.strip():
            continue
        lines.append(f"【来源题号】{part.source_question_no}")
        lines.append(part.text.strip())
    return "\n".join(lines).strip()


def _with_extracted_answer_text(part: StudentAnswerPart) -> StudentAnswerPart:
    if part.answer_text.strip():
        return part

    extracted = ""
    worked_solution_block_detected = False

    tag_segments = _extract_student_tag_segments(part.text)
    if tag_segments:
        extracted = "\n".join(tag_segments)
        worked_solution_block_detected = _is_worked_solution_block_tag(tag_segments)
    else:
        tag_segments = _extract_student_tag_segments(
            "\n".join(element.raw_content for element in part.elements)
        )
        if tag_segments:
            extracted = "\n".join(tag_segments)
            worked_solution_block_detected = _is_worked_solution_block_tag(tag_segments)

    warnings = list(part.extraction_warnings)
    if not extracted:
        extracted = _infer_short_answer_from_elements(part)
        if extracted:
            warnings.append("ANSWER_TEXT_INFERRED_WITHOUT_STUDENT_TAGS")
    if not extracted:
        extracted = _infer_worked_solution_from_ocr(part)
        if extracted:
            warnings.append("ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS")
    if not extracted and part.text.strip() and not part.is_blank:
        warnings.append("NO_STUDENT_TAGS_FOUND")

    return part.model_copy(
        update={
            "answer_text": extracted,
            "extraction_warnings": warnings,
            "worked_solution_block_detected": worked_solution_block_detected,
        }
    )


def _extract_student_tag_segments(text: str) -> list[str]:
    answers = [
        _normalize_student_answer(match.group(1))
        for match in re.finditer(r"<student>(.*?)</student>", text, flags=re.DOTALL | re.IGNORECASE)
    ]
    return _drop_single_letter_noise_when_choice_answer_exists(answers)


def _extract_student_tag_text(text: str) -> str:
    return "\n".join(answer for answer in _extract_student_tag_segments(text) if answer)


def _is_worked_solution_block_tag(segments: list[str]) -> bool:
    if len(segments) != 1:
        return False
    segment = segments[0].strip()
    if not _looks_like_worked_solution(segment):
        return False
    non_empty_lines = [line for line in segment.splitlines() if line.strip()]
    compact_len = len(re.sub(r"\s+", "", segment))
    return len(non_empty_lines) >= 2 or compact_len >= 30


def _normalize_student_answer(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(line for line in lines if line)


def _drop_single_letter_noise_when_choice_answer_exists(answers: list[str]) -> list[str]:
    normalized = [answer for answer in answers if answer]
    has_choice_answer = any(re.fullmatch(r"[A-D]{1,4}", answer.upper()) for answer in normalized)
    if not has_choice_answer:
        return normalized
    return [
        answer
        for answer in normalized
        if not (
            re.fullmatch(r"[A-Za-z]", answer)
            and answer.upper() not in {"A", "B", "C", "D"}
        )
    ]


def _infer_short_answer_from_elements(part: StudentAnswerPart) -> str:
    candidates: list[str] = []
    for element in part.elements:
        content = _normalize_student_answer(element.raw_content)
        if not _looks_like_short_student_answer(content):
            continue
        if element.bbox is not None and element.bbox.x_min > 0.24:
            continue
        candidates.append(content)
    return "\n".join(candidates)


def _infer_worked_solution_from_ocr(part: StudentAnswerPart) -> str:
    text = part.text.strip()
    if not text or not _looks_like_worked_solution(text):
        return ""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"\d+\s*[\.．、]?", line):
            continue
        if line.lower().startswith("the right half of the image is blank"):
            continue
        line = re.sub(r"^\d+\s*[\.．、]\s*", "", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def _looks_like_worked_solution(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 30:
        return False
    markers = ("=", "\\frac", "解", "得", "(1)", "(2)", "N", "kg", "m/s", "周期", "质量")
    return any(marker in text for marker in markers)


def _looks_like_short_student_answer(text: str) -> bool:
    if not text or len(text) > 12:
        return False
    if re.match(r"^\d+\s*[\.．、]", text):
        return False
    if re.match(r"^[A-D]\s*[\.．、]", text, flags=re.IGNORECASE):
        return False
    normalized = re.sub(r"[\s,，、/]+", "", text).upper()
    if re.fullmatch(r"[A-D]{1,4}", normalized):
        return True
    if re.fullmatch(r"[①②③④⑤⑥⑦⑧⑨⑩]{1,6}", normalized):
        return True
    return False


def _build_question_tree(question_ids: Iterable[str]) -> list[QuestionNumber]:
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


def _parent_question_id(question_id: str) -> str:
    parts = [part for part in question_id.split("/") if part]
    if len(parts) > 1 and _is_subquestion_token(parts[-1]):
        return "/".join(parts[:-1])
    return question_id


def _subquestion_slot_from_id(question_id: str) -> str | None:
    parts = [part for part in question_id.split("/") if part]
    if parts and _is_subquestion_token(parts[-1]):
        return parts[-1]
    return None


def _is_subquestion_token(token: str) -> bool:
    return (
        token.startswith("(")
        and token.endswith(")")
        and token[1:-1].isdigit()
    ) or token in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
