from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def summarize_real_paper_smoke(
    manifest: dict[str, Any],
    smoke_result: dict[str, Any],
) -> dict[str, Any]:
    students = smoke_result.get("students")
    if not isinstance(students, list):
        raise ValueError("smoke_result.students must be a list")

    students_by_no = {
        str(item.get("student_no") or ""): item
        for item in students
        if isinstance(item, dict)
    }

    sample_summaries: list[dict[str, Any]] = []
    counts = {"pass": 0, "review": 0, "fail": 0}

    for sample in manifest.get("samples", []):
        if not isinstance(sample, dict):
            continue
        student_no = str(sample.get("student_no") or "").strip()
        smoke_student = students_by_no.get(student_no)
        summary = _summarize_sample(sample, smoke_student)
        sample_summaries.append(summary)
        counts[summary["classification"]] += 1

    return {
        "manifest_name": manifest.get("manifest_name"),
        "manifest_version": manifest.get("manifest_version"),
        "reference_pdf": manifest.get("reference_pdf"),
        "smoke_run_dir": manifest.get("smoke_run_dir"),
        "counts": counts,
        "samples": sample_summaries,
    }


def _summarize_sample(
    sample: dict[str, Any],
    smoke_student: dict[str, Any] | None,
) -> dict[str, Any]:
    expected = sample.get("expected") if isinstance(sample.get("expected"), dict) else {}
    required_questions = [
        str(question_id)
        for question_id in (expected.get("required_scored_questions") or sample.get("question_ids") or [])
    ]
    allowed_paper_review_reasons = {
        str(reason)
        for reason in (expected.get("allowed_paper_review_reasons") or [])
    }
    allowed_question_review_reasons = {
        str(reason)
        for reason in (expected.get("allowed_question_review_reasons") or [])
    }
    allow_review_classification = bool(expected.get("allow_review_classification", True))

    failure_reasons: list[str] = []
    question_results: list[dict[str, Any]] = []
    paper_review_reasons: list[str] = []
    answered_questions = 0
    total_score_deduction = None

    if smoke_student is None:
        failure_reasons.append("missing smoke student result")
    else:
        report = smoke_student.get("report") if isinstance(smoke_student.get("report"), dict) else {}
        per_question = report.get("per_question") if isinstance(report.get("per_question"), dict) else {}
        paper_review_reasons = [str(reason) for reason in (report.get("review_reasons") or [])]
        unexpected_paper_reasons = sorted(set(paper_review_reasons) - allowed_paper_review_reasons)
        if unexpected_paper_reasons:
            failure_reasons.append(
                "unexpected paper review_reasons: " + ",".join(unexpected_paper_reasons)
            )
        answered_questions = int(report.get("answered_questions") or 0)
        total_score_deduction = report.get("total_score_deduction")

        minimum_answered_questions = int(expected.get("minimum_answered_questions") or 0)
        if answered_questions < minimum_answered_questions:
            failure_reasons.append(
                f"answered_questions<{minimum_answered_questions}: {answered_questions}"
            )

        for question_id in required_questions:
            question_report = per_question.get(question_id)
            status = str(question_report.get("status") or "MISSING") if isinstance(question_report, dict) else "MISSING"
            review_reasons = (
                [str(reason) for reason in (question_report.get("review_reasons") or [])]
                if isinstance(question_report, dict)
                else []
            )
            question_results.append(
                {
                    "question_id": question_id,
                    "status": status,
                    "review_reasons": review_reasons,
                    "total_score_deduction": (
                        question_report.get("total_score_deduction")
                        if isinstance(question_report, dict)
                        else None
                    ),
                }
            )
            if status != "SCORED":
                failure_reasons.append(f"{question_id} status={status}")
            unexpected_question_reasons = sorted(
                set(review_reasons) - allowed_question_review_reasons
            )
            if unexpected_question_reasons:
                failure_reasons.append(
                    f"{question_id} unexpected review_reasons: "
                    + ",".join(unexpected_question_reasons)
                )

    if failure_reasons:
        classification = "fail"
    elif paper_review_reasons or any(item["review_reasons"] for item in question_results):
        classification = "review" if allow_review_classification else "fail"
        if classification == "fail":
            failure_reasons.append("review classification is not allowed for this sample")
    else:
        classification = "pass"

    return {
        "sample_id": sample.get("sample_id"),
        "student_no": sample.get("student_no"),
        "classification": classification,
        "answered_questions": answered_questions,
        "total_score_deduction": total_score_deduction,
        "paper_review_reasons": paper_review_reasons,
        "allowed_paper_review_reasons": sorted(allowed_paper_review_reasons),
        "allowed_question_review_reasons": sorted(allowed_question_review_reasons),
        "paper_review_reasons_within_expectation": (
            set(paper_review_reasons).issubset(allowed_paper_review_reasons)
            if paper_review_reasons
            else True
        ),
        "question_results": question_results,
        "failure_reasons": failure_reasons,
        "notes": sample.get("notes") or "",
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary.get('manifest_name') or 'real paper smoke summary'}",
        "",
        f"- manifest_version: `{summary.get('manifest_version')}`",
        f"- reference_pdf: `{summary.get('reference_pdf')}`",
        f"- smoke_run_dir: `{summary.get('smoke_run_dir')}`",
        "",
        "## Counts",
        "",
        f"- pass: {summary.get('counts', {}).get('pass', 0)}",
        f"- review: {summary.get('counts', {}).get('review', 0)}",
        f"- fail: {summary.get('counts', {}).get('fail', 0)}",
        "",
        "## Samples",
        "",
        "| sample_id | student_no | classification | answered_questions | total_score_deduction | paper_review_reasons |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for sample in summary.get("samples", []):
        paper_reasons = "、".join(sample.get("paper_review_reasons") or []) or "无"
        lines.append(
            f"| {sample.get('sample_id')} | {sample.get('student_no')} | {sample.get('classification')} | "
            f"{sample.get('answered_questions')} | {sample.get('total_score_deduction')} | {paper_reasons} |"
        )

    for sample in summary.get("samples", []):
        question_results = sample.get("question_results") or []
        if not question_results:
            continue
        question_results = sample.get("question_results") or []
        lines.append("")
        lines.append(f"### {sample.get('sample_id')}")
        lines.append("")
        for item in question_results:
            question_review = "、".join(item.get("review_reasons") or []) or "无"
            lines.append(
                f"- `{item.get('question_id')}`: status={item.get('status')}, "
                f"deduction={item.get('total_score_deduction')}, review_reasons={question_review}"
            )
        if sample.get("failure_reasons"):
            lines.append(f"- failure_reasons: {'; '.join(sample['failure_reasons'])}")
        if sample.get("notes"):
            lines.append(f"- notes: {sample['notes']}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a real paper smoke run with pass/review/fail buckets.")
    parser.add_argument("--manifest", required=True, help="Path to regression manifest JSON.")
    parser.add_argument("--smoke-result", required=True, help="Path to smoke_result.json.")
    parser.add_argument("--output-json", required=True, help="Where to write the summary JSON.")
    parser.add_argument("--output-md", required=True, help="Where to write the summary Markdown.")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    smoke_result_path = Path(args.smoke_result)
    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)

    summary = summarize_real_paper_smoke(
        _load_json(manifest_path),
        _load_json(smoke_result_path),
    )

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.write_text(render_markdown(summary), encoding="utf-8")
    print(output_json_path)
    print(output_md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
