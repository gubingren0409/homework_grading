from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from src.cognitive.question_tree import match_question_label, normalize_question_label, sort_perception_nodes
from src.schemas.perception_ir import BoundingBox, PerceptionOutput, QuestionAnchor, QuestionAnchorSet
from src.skills.interfaces import LayoutParseResult, LayoutRegion


@dataclass
class _AnchorCandidate:
    raw_label: str
    normalized_label: str
    level: int
    bbox: BoundingBox


class QuestionAnchorDetector:
    def detect_from_perception(
        self,
        perception_data: PerceptionOutput,
        *,
        page_index: int = 0,
    ) -> QuestionAnchorSet:
        candidates, warnings = self._candidates_from_perception(perception_data)
        anchor_set, *_ = self._build_anchor_set_with_stack(
            candidates,
            page_index=page_index,
            source="perception",
            warnings=warnings,
            initial_stack=[],
            initial_seen_paths=set(),
            initial_numeric_seen_by_parent=set(),
            initial_last_numeric_question=None,
        )
        return anchor_set

    def detect_document_from_perceptions(
        self,
        perception_outputs: list[PerceptionOutput],
    ) -> list[QuestionAnchorSet]:
        stack: list[tuple[int, str]] = []
        seen_paths: set[tuple[str, ...]] = set()
        numeric_seen_by_parent: set[tuple[str, ...]] = set()
        last_numeric_question: int | None = None
        anchor_sets: list[QuestionAnchorSet] = []
        for page_index, perception_data in enumerate(perception_outputs):
            candidates, warnings = self._candidates_from_perception(perception_data)
            (
                anchor_set,
                stack,
                seen_paths,
                numeric_seen_by_parent,
                last_numeric_question,
            ) = self._build_anchor_set_with_stack(
                candidates,
                page_index=page_index,
                source="perception",
                warnings=warnings,
                initial_stack=stack,
                initial_seen_paths=seen_paths,
                initial_numeric_seen_by_parent=numeric_seen_by_parent,
                initial_last_numeric_question=last_numeric_question,
            )
            anchor_sets.append(anchor_set)
        return anchor_sets

    def _candidates_from_perception(
        self,
        perception_data: PerceptionOutput,
    ) -> tuple[list[_AnchorCandidate], list[str]]:
        candidates: list[_AnchorCandidate] = []
        warnings: list[str] = []

        for node in sort_perception_nodes(perception_data.elements):
            text = node.raw_content.strip()
            if not text:
                continue
            if node.bbox is None:
                warnings.append(f"missing bbox for perception node {node.element_id}")
                continue
            match = match_question_label(text)
            if match is None:
                continue
            level, raw_label = match
            candidates.append(
                _AnchorCandidate(
                    raw_label=raw_label,
                    normalized_label=normalize_question_label(raw_label),
                    level=level,
                    bbox=node.bbox,
                )
            )
        return candidates, warnings

    def detect_from_layout(
        self,
        layout_data: LayoutParseResult,
    ) -> QuestionAnchorSet:
        warnings = list(layout_data.warnings)
        candidates: list[_AnchorCandidate] = []
        for region in self._sort_regions(layout_data.regions):
            if not region.question_no:
                continue
            raw_label = region.question_no
            candidates.append(
                _AnchorCandidate(
                    raw_label=raw_label,
                    normalized_label=normalize_question_label(raw_label),
                    level=self._infer_level(raw_label),
                    bbox=BoundingBox.model_validate(region.bbox),
                )
            )
        return self._build_anchor_set(
            candidates,
            page_index=layout_data.page_index,
            source="layout",
            warnings=warnings,
        )

    def _build_anchor_set(
        self,
        candidates: Iterable[_AnchorCandidate],
        *,
        page_index: int,
        source: Literal["perception", "layout"],
        warnings: list[str],
    ) -> QuestionAnchorSet:
        anchor_set, *_ = self._build_anchor_set_with_stack(
            candidates,
            page_index=page_index,
            source=source,
            warnings=warnings,
            initial_stack=[],
            initial_seen_paths=set(),
            initial_numeric_seen_by_parent=set(),
            initial_last_numeric_question=None,
        )
        return anchor_set

    def _build_anchor_set_with_stack(
        self,
        candidates: Iterable[_AnchorCandidate],
        *,
        page_index: int,
        source: Literal["perception", "layout"],
        warnings: list[str],
        initial_stack: list[tuple[int, str]],
        initial_seen_paths: set[tuple[str, ...]],
        initial_numeric_seen_by_parent: set[tuple[str, ...]],
        initial_last_numeric_question: int | None,
    ) -> tuple[
        QuestionAnchorSet,
        list[tuple[int, str]],
        set[tuple[str, ...]],
        set[tuple[str, ...]],
        int | None,
    ]:
        stack: list[tuple[int, str]] = list(initial_stack)
        seen_paths: set[tuple[str, ...]] = set(initial_seen_paths)
        numeric_seen_by_parent: set[tuple[str, ...]] = set(initial_numeric_seen_by_parent)
        last_numeric_question = initial_last_numeric_question
        anchors: list[QuestionAnchor] = []
        for order_index, candidate in enumerate(candidates):
            while stack and stack[-1][0] >= candidate.level:
                stack.pop()
            parent_path = [token for _, token in stack]
            candidate_path = tuple([*parent_path, candidate.normalized_label])
            if (
                candidate.level == 1
                and not self._is_plausible_next_numeric(
                    candidate.normalized_label,
                    parent_path=parent_path,
                    numeric_seen_by_parent=numeric_seen_by_parent,
                    last_numeric_question=last_numeric_question,
                )
            ) or candidate_path in seen_paths:
                continue
            question_no = "/".join([*parent_path, candidate.normalized_label])
            anchors.append(
                QuestionAnchor(
                    raw_label=candidate.raw_label,
                    question_no=question_no,
                    page_index=page_index,
                    order_index=order_index,
                    source=source,
                    bbox=candidate.bbox,
                )
            )
            seen_paths.add(candidate_path)
            if candidate.level == 1 and candidate.normalized_label.isdigit():
                numeric_seen_by_parent.add(tuple(parent_path))
                last_numeric_question = int(candidate.normalized_label)
            stack.append((candidate.level, candidate.normalized_label))
        return (
            QuestionAnchorSet(page_index=page_index, anchors=anchors, warnings=warnings),
            stack,
            seen_paths,
            numeric_seen_by_parent,
            last_numeric_question,
        )

    def _infer_level(self, raw_label: str) -> int:
        match = match_question_label(raw_label)
        if match is None:
            return 1
        level, _ = match
        return level

    def _sort_regions(self, regions: list[LayoutRegion]) -> list[LayoutRegion]:
        def _key(region: LayoutRegion) -> tuple[float, float, str]:
            return (
                float(region.bbox.get("y_min", 1.0)),
                float(region.bbox.get("x_min", 1.0)),
                region.target_id,
            )

        return sorted(regions, key=_key)

    def _is_plausible_next_numeric(
        self,
        normalized_label: str,
        *,
        parent_path: list[str],
        numeric_seen_by_parent: set[tuple[str, ...]],
        last_numeric_question: int | None,
    ) -> bool:
        if not normalized_label.isdigit() or last_numeric_question is None:
            return True
        if tuple(parent_path) not in numeric_seen_by_parent:
            return True
        return int(normalized_label) <= last_numeric_question + 1
