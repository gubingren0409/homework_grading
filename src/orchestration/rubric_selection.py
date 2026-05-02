from __future__ import annotations

import json
import re
from typing import Iterable

from src.schemas.rubric_ir import RubricBundle, TeacherRubric


def parse_question_ids(raw_question_ids: str | None) -> list[str]:
    if raw_question_ids is None or not raw_question_ids.strip():
        return []
    raw = raw_question_ids.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        question_ids = [str(item).strip() for item in parsed]
    else:
        question_ids = [
            item.strip()
            for item in re.split(r"[,，\s]+", raw)
            if item.strip()
        ]
    return [item for item in question_ids if item]


def select_rubric_bundle_questions(
    rubric_bundle: RubricBundle,
    requested_question_ids: Iterable[str],
) -> RubricBundle:
    requested = [str(item).strip() for item in requested_question_ids if str(item).strip()]
    if not requested:
        return rubric_bundle

    selected: list[TeacherRubric] = []
    missing: list[str] = []
    for requested_id in requested:
        matches = [
            rubric
            for rubric in rubric_bundle.rubrics
            if _question_id_matches(rubric.question_id, requested_id)
        ]
        if len(matches) == 1:
            selected.append(matches[0])
            continue
        if not matches:
            missing.append(requested_id)
            continue
        matched_ids = ", ".join(rubric.question_id for rubric in matches)
        raise ValueError(f"question_id {requested_id!r} matched multiple rubrics: {matched_ids}")

    if missing:
        available = ", ".join(rubric.question_id for rubric in rubric_bundle.rubrics)
        raise ValueError(f"missing question_ids {missing}; available rubrics: {available}")

    return RubricBundle(
        paper_id=rubric_bundle.paper_id,
        rubrics=selected,
        question_tree=rubric_bundle.question_tree,
    )


def validate_rubric_solution_content(
    rubric_bundle: RubricBundle,
    requested_question_ids: Iterable[str],
) -> None:
    subset = select_rubric_bundle_questions(rubric_bundle, requested_question_ids)
    incomplete = [
        rubric.question_id
        for rubric in subset.rubrics
        if not _has_solution_content(rubric)
    ]
    if incomplete:
        raise ValueError(
            "rubric extraction missing answer/solution content for question_ids: "
            + ", ".join(incomplete)
        )


def _question_id_matches(rubric_question_id: str, requested_question_id: str) -> bool:
    rubric_id = _normalize_question_id(rubric_question_id)
    requested_id = _normalize_question_id(requested_question_id)
    if rubric_id == requested_id:
        return True
    return _last_token(rubric_id) == requested_id


def _normalize_question_id(question_id: str) -> str:
    return re.sub(r"\s+", "", str(question_id).strip()).strip("/")


def _last_token(question_id: str) -> str:
    parts = [part for part in _normalize_question_id(question_id).split("/") if part]
    return parts[-1] if parts else _normalize_question_id(question_id)


def _has_solution_content(rubric: TeacherRubric) -> bool:
    if rubric.grading_points:
        return True
    answer = rubric.correct_answer.strip()
    if len(answer) < 20:
        return False
    solution_markers = ("【答案】", "答案", "【详解】", "详解", "第(1)部分", "解得", "故")
    return any(marker in answer for marker in solution_markers)
