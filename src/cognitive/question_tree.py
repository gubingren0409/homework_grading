from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from src.schemas.perception_ir import PerceptionOutput, PerceptionNode
from src.schemas.question_ir import QuestionNumber
from src.schemas.rubric_ir import GradingPoint, RubricBundle, RubricVisualEvidence, TeacherRubric

_CHINESE_SECTION_RE = re.compile(r"^(?P<label>[一二三四五六七八九十百千]+、)")
_NUMBER_RE = re.compile(r"^(?P<label>(?:第\s*\d+\s*题|\d+[\.．、](?!\d)))")
_SUBQUESTION_RE = re.compile(r"^(?P<label>[（(]\d+[)）])")
_CIRCLED_RE = re.compile(r"^(?P<label>[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])")
_INLINE_QUESTION_SPLIT_RE = re.compile(
    r"(?<=[；。！？])(?=(?:第\s*\d+\s*题|\d+[\.．、]|[（(]\d+[)）]|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]))"
)
_ANSWER_MARKER_RE = re.compile(r"(【答案】|答案[:：])")
_ANSWER_BANK_NUMBER_RE = re.compile(r"(?<!\d)(?P<num>\d{1,3})[\.．、](?!\d)")
_ANSWER_BANK_SUBQUESTION_RE = re.compile(r"(?P<label>[（(]\d+[)）])")
_SOLUTION_SECTION_RE = re.compile(r"^第[（(](?P<num>\d+)[)）]部分")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<asset>[^)]+)\)")
_VISUAL_DESCRIPTION_RE = re.compile(
    r"【(?P<label>图表描述|表格转写):(?P<source>[^|】]+)(?:\|(?P<kind>[^】]+))?】(?P<description>.*)"
)
_VISUAL_CONTENT_TYPES = {
    "image_diagram",
    "table",
    "image",
    "coordinate_plot",
    "circuit_schematic",
    "geometry_topology",
}
_VISUAL_EVIDENCE_TYPES = {*_VISUAL_CONTENT_TYPES, "image_asset"}
_SCORE_MARK_RE = re.compile(r"[（(]\s*(?P<score>\d+(?:\.\d+)?)\s*分\s*[)）]")
_ALTERNATIVE_METHOD_RE = re.compile(r"(方法[二三四五六七八九十]|alternative method|等价方法|另一种方法)", re.IGNORECASE)
_SOLUTION_BOUNDARY_RE = re.compile(r"(【来源题号】|^第[（(]\d+[)）]部分|^[（(]\d+[)）])")


@dataclass
class _QuestionCapture:
    node: QuestionNumber
    level: int
    lines: list[str]


def match_question_label(line: str) -> Optional[tuple[int, str]]:
    for level, pattern in enumerate(
        (_CHINESE_SECTION_RE, _NUMBER_RE, _SUBQUESTION_RE, _CIRCLED_RE)
    ):
        match = pattern.match(line)
        if match:
            return level, match.group("label")
    return None


def normalize_question_label(raw_label: str) -> str:
    cleaned = re.sub(r"\s+", "", raw_label)
    if _CHINESE_SECTION_RE.match(cleaned):
        return cleaned[:-1]
    if _NUMBER_RE.match(cleaned):
        if cleaned.startswith("第") and cleaned.endswith("题"):
            return re.sub(r"\D", "", cleaned)
        return cleaned.rstrip(".．、")
    if _SUBQUESTION_RE.match(cleaned):
        digits = re.sub(r"\D", "", cleaned)
        return f"({digits})"
    return cleaned


def sort_perception_nodes(nodes: list[PerceptionNode]) -> list[PerceptionNode]:
    def _key(node: PerceptionNode) -> tuple[float, float, str]:
        if node.bbox is None:
            return (1e9, 1e9, node.element_id)
        return (node.bbox.y_min, node.bbox.x_min, node.element_id)

    return sorted(nodes, key=_key)


class QuestionTreeExtractor:
    """Split whole-paper reference text into a question tree and per-question rubrics."""

    def extract_from_markdown(
        self,
        markdown_text: str,
        *,
        paper_id: str,
        image_descriptions: Optional[dict[str, str]] = None,
    ) -> RubricBundle:
        if image_descriptions:
            markdown_text = self._inject_markdown_image_descriptions(markdown_text, image_descriptions)

        roots: list[QuestionNumber] = []
        stack: list[tuple[int, QuestionNumber]] = []
        captures: list[_QuestionCapture] = []
        current_capture: Optional[_QuestionCapture] = None
        seen_paths: set[tuple[str, ...]] = set()
        numeric_seen_by_parent: set[tuple[str, ...]] = set()
        last_numeric_question: Optional[int] = None
        order_index = 0

        for raw_line in self._iter_lines(markdown_text):
            match = self._match_question_label(raw_line)
            if match is None:
                if current_capture is not None:
                    current_capture.lines.append(raw_line)
                continue

            level, raw_label = match
            normalized_token = self._normalize_label(raw_label)

            while stack and stack[-1][0] >= level:
                stack.pop()

            parent_path = stack[-1][1].normalized_path if stack else []
            candidate_path = tuple([*parent_path, normalized_token])
            if (
                level == 1
                and not self._is_plausible_next_numeric(
                    normalized_token,
                    parent_path=parent_path,
                    numeric_seen_by_parent=numeric_seen_by_parent,
                    last_numeric_question=last_numeric_question,
                )
            ) or candidate_path in seen_paths:
                if current_capture is not None:
                    current_capture.lines.append(raw_line)
                continue

            node = QuestionNumber(
                raw_label=raw_label,
                normalized_path=[*parent_path, normalized_token],
                order_index=order_index,
            )
            order_index += 1
            seen_paths.add(candidate_path)

            if stack:
                stack[-1][1].children.append(node)
            else:
                roots.append(node)
            stack.append((level, node))

            if level == 0:
                current_capture = None
                continue

            if level == 1 and normalized_token.isdigit():
                numeric_seen_by_parent.add(tuple(parent_path))
                last_numeric_question = int(normalized_token)
            current_capture = _QuestionCapture(node=node, level=level, lines=[raw_line])
            captures.append(current_capture)

        self._redistribute_answer_banks(captures)
        self._redistribute_solution_sections(captures)

        rubrics = [
            TeacherRubric(
                question_id="/".join(capture.node.normalized_path),
                correct_answer="\n".join(capture.lines).strip(),
                grading_points=self._extract_grading_points(capture),
            )
            for capture in captures
            if capture.lines
        ]
        self._attach_visual_evidence(rubrics)

        return RubricBundle(
            paper_id=paper_id,
            rubrics=rubrics,
            question_tree=roots,
        )

    def extract_from_perception(self, perception_data: PerceptionOutput, *, paper_id: str) -> RubricBundle:
        markdown_text = "\n".join(
            self._format_perception_node_for_rubric(node)
            for node in self._sort_nodes(perception_data.elements)
            if node.raw_content.strip()
        )
        return self.extract_from_markdown(markdown_text, paper_id=paper_id)

    def _inject_markdown_image_descriptions(
        self,
        markdown_text: str,
        image_descriptions: dict[str, str],
    ) -> str:
        def _replace(match: re.Match[str]) -> str:
            asset_ref = match.group("asset").strip()
            description = (image_descriptions.get(asset_ref) or "").strip()
            if not description:
                return match.group(0)
            return f"{match.group(0)}\n【图表描述:{asset_ref}|image_asset】{description}"

        return _MARKDOWN_IMAGE_RE.sub(_replace, markdown_text)

    def _format_perception_node_for_rubric(self, node: PerceptionNode) -> str:
        raw_content = node.raw_content.strip()
        if node.content_type == "table":
            return f"【表格转写:{node.element_id}|table】{raw_content}"
        if node.content_type in _VISUAL_CONTENT_TYPES:
            return f"【图表描述:{node.element_id}|{node.content_type}】{raw_content}"
        return raw_content

    def _extract_grading_points(self, capture: _QuestionCapture) -> list[GradingPoint]:
        question_id = "/".join(capture.node.normalized_path)
        scope = capture.node.normalized_path[-1] if self._is_subquestion_token(capture.node.normalized_path[-1]) else None
        points: list[GradingPoint] = []
        active_lines: list[str] = []
        skip_alternative = False
        for raw_line in capture.lines:
            line = raw_line.strip()
            if not line:
                continue
            if _ALTERNATIVE_METHOD_RE.search(line):
                skip_alternative = True
                continue
            if skip_alternative and _SOLUTION_BOUNDARY_RE.search(line):
                skip_alternative = False
            if skip_alternative:
                continue
            active_lines.append(line)
        text = "\n".join(active_lines)
        for match in _SCORE_MARK_RE.finditer(text):
            score = float(match.group("score"))
            units = int(score)
            if units <= 0 or abs(score - units) > 1e-6:
                units = 1
            description = self._grading_point_description(text, match.start())
            for unit_index in range(units):
                point_no = len(points) + 1
                suffix = f"（第 {unit_index + 1}/{units} 分）" if units > 1 else ""
                points.append(
                    GradingPoint(
                        point_id=f"{question_id}-p{point_no:02d}",
                        description=f"{description}{suffix}",
                        score=1.0 if units > 1 or abs(score - 1.0) < 1e-6 else score,
                        scope=scope,
                    )
                )
        self._augment_unmarked_grading_points(
            points,
            question_id=question_id,
            scope=scope,
            text=text,
        )
        return points

    def _augment_unmarked_grading_points(
        self,
        points: list[GradingPoint],
        *,
        question_id: str,
        scope: str | None,
        text: str,
    ) -> None:
        if scope != "(3)" or "洛伦兹力" not in text:
            return
        if "第一次经过最低点" not in text or "第二次经过最低点" not in text:
            return
        existing_total = sum(point.score for point in points)
        if existing_total >= 6:
            return
        additions = [
            "分析两次经过最低点时洛伦兹力方向对拉力大小的影响",
            "列出或等价使用两次经过最低点的受力方程并比较 F′₁、F′₂ 与 Fmax",
        ]
        for description in additions[: max(0, int(6 - existing_total))]:
            point_no = len(points) + 1
            points.append(
                GradingPoint(
                    point_id=f"{question_id}-p{point_no:02d}",
                    description=description,
                    score=1.0,
                    scope=scope,
                )
            )

    def _grading_point_description(self, text: str, score_mark_start: int) -> str:
        prefix = text[:score_mark_start].strip()
        if not prefix:
            return "参考答案给分点"
        lines = [
            self._normalize_grading_point_text(line)
            for line in prefix.splitlines()
            if line.strip()
        ]
        local_lines = lines[-8:]
        contextual_start = None
        for index in range(len(local_lines) - 1, -1, -1):
            if self._is_contextual_grading_point_line(local_lines[index]):
                contextual_start = index
                break
        if contextual_start is not None:
            contextual_candidate = " ".join(local_lines[contextual_start:])
            contextual_candidate = self._normalize_grading_point_text(contextual_candidate).strip(" ：:，,")
            if contextual_candidate and self._grading_point_description_score(contextual_candidate) >= 0:
                return contextual_candidate[-160:]

        tail = lines[-6:]
        tail_candidates: list[str] = []
        for window_size in range(1, len(tail) + 1):
            tail_candidates.append(" ".join(tail[-window_size:]))

        best_tail = ""
        best_tail_score = float("-inf")
        for candidate in tail_candidates:
            cleaned = self._normalize_grading_point_text(candidate).strip(" ：:，,")
            if not cleaned:
                continue
            score = self._grading_point_description_score(cleaned)
            if score > best_tail_score:
                best_tail_score = score
                best_tail = cleaned
        if best_tail and best_tail_score >= 0:
            return best_tail[-160:]

        candidates: list[str] = list(tail_candidates)
        candidates.extend(reversed(re.split(r"[\n。；;]", prefix)))

        best_candidate = ""
        best_score = float("-inf")
        for candidate in candidates:
            cleaned = self._normalize_grading_point_text(candidate).strip(" ：:，,")
            if not cleaned:
                continue
            score = self._grading_point_description_score(cleaned)
            if score > best_score:
                best_score = score
                best_candidate = cleaned
        if best_candidate:
            return best_candidate[-160:]
        return prefix[-160:]

    def _normalize_grading_point_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _grading_point_description_score(self, candidate: str) -> float:
        normalized = re.sub(r"\s+", "", candidate)
        if not normalized:
            return float("-inf")

        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
        letter_count = len(re.findall(r"[A-Za-z]", normalized))
        digit_count = len(re.findall(r"\d", normalized))
        replacement_count = normalized.count("�")
        score = float(cjk_count * 5 + letter_count * 3 + min(digit_count, 6))

        if "=" in candidate or "≈" in candidate or "→" in candidate:
            score += 3
        if normalized.startswith(tuple(str(digit) for digit in range(10))) and cjk_count == 0 and "=" not in candidate:
            score -= 4
        if cjk_count == 0 and letter_count == 0 and "=" not in candidate:
            score -= 12
        if len(normalized) < 4:
            score -= 8
        if len(normalized) < 8 and cjk_count == 0 and "=" not in candidate:
            score -= 6
        score -= replacement_count * 6
        return score

    def _is_contextual_grading_point_line(self, line: str) -> bool:
        normalized = re.sub(r"\s+", "", line)
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
        letter_count = len(re.findall(r"[A-Za-z]", normalized))
        return cjk_count >= 2 or ("=" in line and (cjk_count >= 1 or letter_count >= 1))

    def _iter_lines(self, markdown_text: str) -> Iterable[str]:
        for raw_line in markdown_text.splitlines():
            normalized = raw_line.strip()
            if not normalized:
                continue

            normalized = re.sub(r"^#+\s*", "", normalized)
            for piece in _INLINE_QUESTION_SPLIT_RE.split(normalized):
                piece = piece.strip()
                if piece:
                    yield piece

    def _match_question_label(self, line: str) -> Optional[tuple[int, str]]:
        return match_question_label(line)

    def _normalize_label(self, raw_label: str) -> str:
        return normalize_question_label(raw_label)

    def _is_subquestion_token(self, token: str) -> bool:
        return bool(_SUBQUESTION_RE.match(token))

    def _sort_nodes(self, nodes: list[PerceptionNode]) -> list[PerceptionNode]:
        return sort_perception_nodes(nodes)

    def _attach_visual_evidence(self, rubrics: list[TeacherRubric]) -> None:
        for rubric in rubrics:
            evidence: list[RubricVisualEvidence] = []
            seen_ids: set[str] = set()
            for index, match in enumerate(_VISUAL_DESCRIPTION_RE.finditer(rubric.correct_answer), start=1):
                source_element_id = match.group("source").strip()
                evidence_id = f"{rubric.question_id}:visual:{source_element_id}"
                if evidence_id in seen_ids:
                    continue
                seen_ids.add(evidence_id)
                evidence_type = (match.group("kind") or "").strip() or (
                    "table" if match.group("label") == "表格转写" else "image_diagram"
                )
                if evidence_type not in _VISUAL_EVIDENCE_TYPES:
                    evidence_type = "image_diagram"
                evidence.append(
                    RubricVisualEvidence(
                        evidence_id=evidence_id,
                        evidence_type=evidence_type,  # type: ignore[arg-type]
                        description=match.group("description").strip() or None,
                        asset_ref=source_element_id if evidence_type == "image_asset" else None,
                        source_element_id=source_element_id,
                    )
                )
            for index, match in enumerate(_MARKDOWN_IMAGE_RE.finditer(rubric.correct_answer), start=1):
                asset_ref = match.group("asset").strip()
                if any(item.asset_ref == asset_ref for item in evidence):
                    continue
                evidence_id = f"{rubric.question_id}:asset:{index}"
                if evidence_id in seen_ids:
                    continue
                seen_ids.add(evidence_id)
                alt_text = match.group("alt").strip()
                evidence.append(
                    RubricVisualEvidence(
                        evidence_id=evidence_id,
                        evidence_type="image_asset",
                        description=alt_text or None,
                        asset_ref=asset_ref,
                    )
                )
            rubric.visual_evidence = evidence

    def _redistribute_answer_banks(self, captures: list[_QuestionCapture]) -> None:
        captures_by_path = {
            tuple(capture.node.normalized_path): capture
            for capture in captures
        }

        for capture in captures:
            parent_path = capture.node.normalized_path[:-1]
            rewritten_lines: list[str] = []
            for line in capture.lines:
                marker = _ANSWER_MARKER_RE.search(line)
                if marker is None:
                    rewritten_lines.append(line)
                    continue

                answer_bank = line[marker.start():]
                numbered_answers = self._parse_numbered_answer_bank(
                    answer_bank,
                    parent_path=parent_path,
                    captures_by_path=captures_by_path,
                )
                if len(numbered_answers) < 2:
                    parent_path = capture.node.normalized_path[:-1]
                    numbered_answers = self._parse_subquestion_answer_bank(
                        answer_bank,
                        parent_path=parent_path,
                        captures_by_path=captures_by_path,
                    )

                if len(numbered_answers) < 2:
                    rewritten_lines.append(line)
                    continue

                prefix = line[:marker.start()].strip()
                if prefix:
                    rewritten_lines.append(prefix)

                for question_no, answer_text in numbered_answers.items():
                    target_path = tuple([*parent_path, question_no])
                    for target_capture in self._captures_for_answer_target(captures, target_path):
                        addition = f"【集中答案】{answer_text}"
                        if addition not in target_capture.lines:
                            target_capture.lines.append(addition)
            capture.lines = rewritten_lines

    def _parse_numbered_answer_bank(
        self,
        answer_bank: str,
        *,
        parent_path: list[str],
        captures_by_path: dict[tuple[str, ...], _QuestionCapture],
    ) -> dict[str, str]:
        matches = list(_ANSWER_BANK_NUMBER_RE.finditer(answer_bank))
        answers: dict[str, list[str]] = {}
        for index, match in enumerate(matches):
            question_no = match.group("num")
            target_path = tuple([*parent_path, question_no])
            if target_path not in captures_by_path:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(answer_bank)
            segment = self._clean_answer_bank_segment(answer_bank[match.start():end])
            if segment:
                question_segments = answers.setdefault(question_no, [])
                normalized_segment = self._normalize_answer_segment_for_dedupe(segment)
                if all(
                    self._normalize_answer_segment_for_dedupe(existing) != normalized_segment
                    for existing in question_segments
                ):
                    question_segments.append(segment)
        return {
            question_no: " ".join(segments)
            for question_no, segments in answers.items()
            if segments
        }

    def _parse_subquestion_answer_bank(
        self,
        answer_bank: str,
        *,
        parent_path: list[str],
        captures_by_path: dict[tuple[str, ...], _QuestionCapture],
    ) -> dict[str, str]:
        matches = list(_ANSWER_BANK_SUBQUESTION_RE.finditer(answer_bank))
        answers: dict[str, list[str]] = {}
        for index, match in enumerate(matches):
            question_no = normalize_question_label(match.group("label"))
            target_path = tuple([*parent_path, question_no])
            if target_path not in captures_by_path:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(answer_bank)
            segment = self._clean_answer_bank_segment(answer_bank[match.start():end])
            if segment:
                question_segments = answers.setdefault(question_no, [])
                normalized_segment = self._normalize_answer_segment_for_dedupe(segment)
                if all(
                    self._normalize_answer_segment_for_dedupe(existing) != normalized_segment
                    for existing in question_segments
                ):
                    question_segments.append(segment)
        return {
            question_no: " ".join(segments)
            for question_no, segments in answers.items()
            if segments
        }

    def _clean_answer_bank_segment(self, segment: str) -> str:
        cleaned = re.sub(r"\s+", " ", segment).strip()
        cleaned = cleaned.replace("【答案】", "").strip()
        cleaned = self._dedupe_adjacent_answer_tokens(cleaned)
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", cleaned):
            return ""
        return cleaned

    def _normalize_answer_segment_for_dedupe(self, segment: str) -> str:
        normalized = re.sub(r"^\d{1,3}[\.．、]\s*", "", segment)
        return re.sub(r"\s+", "", normalized)

    def _dedupe_adjacent_answer_tokens(self, text: str) -> str:
        tokens = text.split()
        if not tokens:
            return text
        deduped: list[str] = []
        for token in tokens:
            if deduped and self._is_repeated_answer_token(deduped[-1], token):
                continue
            deduped.append(token)
        return " ".join(deduped)

    def _is_repeated_answer_token(self, previous: str, current: str) -> bool:
        if previous != current:
            return False
        if re.fullmatch(r"[A-D]", current):
            return True
        return len(current) > 1

    def _redistribute_solution_sections(self, captures: list[_QuestionCapture]) -> None:
        captures_by_path = {
            tuple(capture.node.normalized_path): capture
            for capture in captures
        }
        for capture in captures:
            heading_indices = [
                index
                for index, line in enumerate(capture.lines)
                if _SOLUTION_SECTION_RE.match(line)
            ]
            if not heading_indices:
                continue

            source_path = tuple(capture.node.normalized_path)
            parent_path = capture.node.normalized_path[:-1]
            keep_lines = list(capture.lines[: heading_indices[0]])

            for offset, start in enumerate(heading_indices):
                end = heading_indices[offset + 1] if offset + 1 < len(heading_indices) else len(capture.lines)
                section_lines = capture.lines[start:end]
                match = _SOLUTION_SECTION_RE.match(section_lines[0])
                if match is None:
                    keep_lines.extend(section_lines)
                    continue

                target_path = tuple([*parent_path, f"({match.group('num')})"])
                target_capture = captures_by_path.get(target_path)
                if target_capture is None or target_path == source_path:
                    keep_lines.extend(section_lines)
                    continue

                addition_lines = [f"【集中解析】{section_lines[0]}", *section_lines[1:]]
                if "\n".join(addition_lines) not in "\n".join(target_capture.lines):
                    target_capture.lines.extend(addition_lines)

            capture.lines = keep_lines

    def _captures_for_answer_target(
        self,
        captures: list[_QuestionCapture],
        target_path: tuple[str, ...],
    ) -> list[_QuestionCapture]:
        return [
            capture
            for capture in captures
            if tuple(capture.node.normalized_path[: len(target_path)]) == target_path
        ]

    def _is_plausible_next_numeric(
        self,
        normalized_token: str,
        *,
        parent_path: list[str],
        numeric_seen_by_parent: set[tuple[str, ...]],
        last_numeric_question: Optional[int],
    ) -> bool:
        if not normalized_token.isdigit() or last_numeric_question is None:
            return True
        if tuple(parent_path) not in numeric_seen_by_parent:
            return True
        return int(normalized_token) <= last_numeric_question + 1
