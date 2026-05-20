"""
Helper functions for paper grading report processing.

This module contains utility functions for processing paper grading reports,
including evidence lookup, report enrichment, and data transformations.
"""
import re
from typing import Any, Dict, List
from urllib.parse import quote

from src.api.utils import safe_get_dict, safe_get_list, iter_list_items, iter_dict_values
from src.core.constants import MAX_EVIDENCE_SNIPPET_LENGTH


def paper_report_answered_question_ids(paper_report: dict[str, Any]) -> set[str]:
    """Extract question IDs that have student answers."""
    bundle = safe_get_dict(paper_report, "student_answer_bundle")
    answers = safe_get_list(bundle, "answers")
    return {
        str(answer.get("question_id"))
        for answer in answers
        if answer.get("question_id")
    }


def paper_report_evidence_lookup(paper_report: Dict[str, Any]) -> Dict[str, str]:
    """Build a lookup table of evidence snippets from student answers."""
    bundle = safe_get_dict(paper_report, "student_answer_bundle")
    answers = safe_get_list(bundle, "answers")

    lookup: Dict[str, str] = {}
    for answer in iter_list_items({"answers": answers}, "answers"):
        question_id = str(answer.get("question_id") or "").strip()
        parts = safe_get_list(answer, "parts")
        if not question_id:
            continue
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            elements = safe_get_list(part, "elements")
            for element_index, element in enumerate(elements):
                if not isinstance(element, dict):
                    continue
                element_id = str(element.get("element_id") or "").strip()
                raw = str(element.get("raw_content") or "").strip()
                if not element_id or not raw:
                    continue
                snippet = raw[:MAX_EVIDENCE_SNIPPET_LENGTH]
                lookup[element_id] = snippet
                transformed_id = f"answer_{question_id}_part{part_index}_{element_index}_{element_id}"
                lookup[transformed_id] = snippet
                lookup[f"p0_{transformed_id}"] = snippet
    return lookup


def enrich_paper_report_evidence(paper_report: Any) -> Any:
    """Enrich paper report with evidence snippets for step evaluations."""
    if not isinstance(paper_report, dict):
        return paper_report
    evidence_lookup = paper_report_evidence_lookup(paper_report)
    per_question = safe_get_dict(paper_report, "per_question")
    if not evidence_lookup:
        return paper_report
    for question_report in iter_dict_values({"per_question": per_question}, "per_question"):
        steps = safe_get_list(question_report, "step_evaluations")
        for step in steps:
            if not isinstance(step, dict) or step.get("evidence_snippet"):
                continue
            ref_id = str(step.get("reference_element_id") or "").strip()
            if not ref_id:
                continue
            evidence = evidence_lookup.get(ref_id)
            if evidence is None:
                evidence = evidence_lookup.get(re.sub(r"^p\d+_", "", ref_id))
            if evidence:
                step["evidence_snippet"] = evidence
    return paper_report


def paper_report_input_images(task_id: str, paper_report: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    """Build input image URLs for paper report."""
    crop_files_by_question = paper_report_crop_files(paper_report)
    if crop_files_by_question:
        images: Dict[str, List[Dict[str, str]]] = {}
        for question_id, crop_files in crop_files_by_question.items():
            image_items = [
                {
                    "name": name,
                    "url": (
                        f"/api/v1/grade/paper/inputs?task_id={quote(task_id)}"
                        f"&question_id={quote(question_id, safe='')}&index={idx}&asset_kind=crop"
                    ),
                }
                for idx, (name, _path) in enumerate(crop_files)
            ]
            images[question_id] = image_items
        return images

    refs_by_question = paper_report.get("input_filenames_by_question")
    names_by_question = paper_report.get("input_filenames_by_question")
    if not isinstance(refs_by_question, dict):
        return {}
    if not isinstance(names_by_question, dict):
        names_by_question = {}

    images: Dict[str, List[Dict[str, str]]] = {}
    for question_key, file_refs in refs_by_question.items():
        if not isinstance(file_refs, list):
            continue
        file_names = names_by_question.get(question_key, [])
        if not isinstance(file_names, list):
            file_names = []
        image_items = [
            {
                "name": file_names[idx] if idx < len(file_names) else f"input_{idx}",
                "url": (
                    f"/api/v1/grade/paper/inputs?task_id={quote(task_id)}"
                    f"&question_id={quote(question_key, safe='')}&index={idx}"
                ),
            }
            for idx, _ref in enumerate(file_refs)
        ]
        if image_items:
            images[question_key] = image_items
    return images


def paper_report_crop_files(paper_report: Dict[str, Any]) -> Dict[str, List[tuple[str, str]]]:
    """Extract crop file paths from paper report."""
    bundle = safe_get_dict(paper_report, "student_answer_bundle")
    answers = safe_get_list(bundle, "answers")

    crops: Dict[str, List[tuple[str, str]]] = {}
    for answer in iter_list_items({"answers": answers}, "answers"):
        question_id = str(answer.get("question_id") or "").strip()
        parts = safe_get_list(answer, "parts")
        if not question_id:
            continue

        crop_items: List[tuple[str, str]] = []
        for index, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                continue
            crop_path = str(part.get("crop_path") or "").strip()
            if not crop_path:
                continue
            crop_index = part.get("crop_index")
            if not isinstance(crop_index, int) or crop_index <= 0:
                crop_index = index
            page_index = part.get("page_index")
            label = f"作答切图 {crop_index}"
            if isinstance(page_index, int) and page_index >= 0:
                label = f"{label}（第{page_index + 1}页）"
            crop_items.append((label, crop_path))
        if crop_items:
            crops[question_id] = crop_items
    return crops


def base_paper_student_id(student_id: Any) -> str:
    """Extract base student ID from potentially suffixed ID."""
    raw = str(student_id or "").strip()
    return raw.split("_")[0] if raw else ""
