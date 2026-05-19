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
    *,
    smoke_run_dir: str | None = None,
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
        "smoke_run_dir": smoke_run_dir or manifest.get("smoke_run_dir"),
        "counts": counts,
        "runtime_totals": _summarize_runtime_totals(sample_summaries),
        "runtime_span_summary": _summarize_runtime_spans(sample_summaries),
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
    runtime_profile: dict[str, Any] = {}

    if smoke_student is None:
        failure_reasons.append("missing smoke student result")
    else:
        report = smoke_student.get("report") if isinstance(smoke_student.get("report"), dict) else {}
        per_question = report.get("per_question") if isinstance(report.get("per_question"), dict) else {}
        runtime_profile = (
            report.get("runtime_profile")
            if isinstance(report.get("runtime_profile"), dict)
            else {}
        )
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
        "runtime_profile": runtime_profile,
        "runtime_total_seconds": _runtime_stage_seconds(runtime_profile, "total"),
        "runtime_diagnostics": _build_runtime_diagnostics(runtime_profile),
        "question_results": question_results,
        "failure_reasons": failure_reasons,
        "notes": sample.get("notes") or "",
    }


def _runtime_stage_seconds(runtime_profile: dict[str, Any], stage: str) -> float | None:
    stage_seconds = runtime_profile.get("stage_seconds")
    if not isinstance(stage_seconds, dict):
        return None
    value = stage_seconds.get(stage)
    if value is None:
        return None
    return float(value)


def _summarize_runtime_totals(samples: list[dict[str, Any]]) -> dict[str, float]:
    totals = [
        float(sample["runtime_total_seconds"])
        for sample in samples
        if sample.get("runtime_total_seconds") is not None
    ]
    if not totals:
        return {}
    total = sum(totals)
    return {
        "sample_count": float(len(totals)),
        "total_seconds": round(total, 3),
        "avg_seconds": round(total / len(totals), 3),
        "max_seconds": round(max(totals), 3),
    }


def _build_runtime_diagnostics(runtime_profile: dict[str, Any]) -> dict[str, Any]:
    stage_seconds = runtime_profile.get("stage_seconds")
    spans = runtime_profile.get("spans")
    diagnostics: dict[str, Any] = {}
    if isinstance(stage_seconds, dict) and stage_seconds:
        slowest_stage_name, slowest_stage_seconds = max(
            (
                (str(stage), float(value))
                for stage, value in stage_seconds.items()
                if stage != "total"
            ),
            key=lambda item: item[1],
            default=(None, None),
        )
        if slowest_stage_name is not None:
            diagnostics["slowest_stage"] = {
                "stage": slowest_stage_name,
                "elapsed_seconds": round(float(slowest_stage_seconds), 3),
            }
    if isinstance(spans, list) and spans:
        valid_spans = [
            span for span in spans
            if isinstance(span, dict) and span.get("elapsed_seconds") is not None
        ]
        if valid_spans:
            slowest_span = max(valid_spans, key=lambda span: float(span.get("elapsed_seconds") or 0.0))
            diagnostics["slowest_span"] = {
                "stage": slowest_span.get("stage"),
                "elapsed_seconds": round(float(slowest_span.get("elapsed_seconds") or 0.0), 3),
                "provider": slowest_span.get("provider"),
                "model": slowest_span.get("model"),
                "item_refs": slowest_span.get("item_refs") or [],
                "retry_count": int(slowest_span.get("retry_count") or 0),
                "fallback_count": int(slowest_span.get("fallback_count") or 0),
                "error_types": list(slowest_span.get("error_types") or []),
            }
            diagnostics["retry_count"] = int(
                sum(int(span.get("retry_count") or 0) for span in valid_spans)
            )
            diagnostics["fallback_count"] = int(
                sum(int(span.get("fallback_count") or 0) for span in valid_spans)
            )
            diagnostics["error_types"] = sorted(
                {
                    str(error_type)
                    for span in valid_spans
                    for error_type in (span.get("error_types") or [])
                    if error_type
                }
            )
    return diagnostics


def _summarize_runtime_spans(samples: list[dict[str, Any]]) -> dict[str, Any]:
    span_records: list[dict[str, Any]] = []
    for sample in samples:
        runtime_profile = sample.get("runtime_profile")
        spans = runtime_profile.get("spans") if isinstance(runtime_profile, dict) else None
        if not isinstance(spans, list):
            continue
        for span in spans:
            if not isinstance(span, dict):
                continue
            span_records.append(
                {
                    "sample_id": sample.get("sample_id"),
                    "student_no": sample.get("student_no"),
                    **span,
                }
            )
    if not span_records:
        return {}
    stage_groups: dict[str, list[float]] = {}
    for span in span_records:
        stage = str(span.get("stage") or "")
        elapsed = span.get("elapsed_seconds")
        if not stage or elapsed is None:
            continue
        stage_groups.setdefault(stage, []).append(float(elapsed))
    return {
        "span_count": len(span_records),
        "slowest_spans": [
            {
                "sample_id": span.get("sample_id"),
                "student_no": span.get("student_no"),
                "stage": span.get("stage"),
                "elapsed_seconds": round(float(span.get("elapsed_seconds") or 0.0), 3),
                "provider": span.get("provider"),
                "model": span.get("model"),
                "item_refs": span.get("item_refs") or [],
                "retry_count": int(span.get("retry_count") or 0),
                "fallback_count": int(span.get("fallback_count") or 0),
                "error_types": list(span.get("error_types") or []),
            }
            for span in sorted(
                span_records,
                key=lambda record: float(record.get("elapsed_seconds") or 0.0),
                reverse=True,
            )[:5]
        ],
        "stage_stats": {
            stage: {
                "count": len(values),
                "avg_seconds": round(sum(values) / len(values), 3),
                "p50_seconds": round(_percentile(values, 0.5), 3),
                "p95_seconds": round(_percentile(values, 0.95), 3),
                "p99_seconds": round(_percentile(values, 0.99), 3),
                "max_seconds": round(max(values), 3),
            }
            for stage, values in sorted(stage_groups.items())
        },
    }


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    target = max(0.0, min(1.0, ratio)) * (len(ordered) - 1)
    lower = int(target)
    upper = min(len(ordered) - 1, lower + 1)
    weight = target - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


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
    ]
    runtime_totals = summary.get("runtime_totals") or {}
    if runtime_totals:
        lines.extend(
            [
                "",
                "## Runtime",
                "",
                f"- sample_count: {int(runtime_totals.get('sample_count', 0))}",
                f"- total_seconds: {runtime_totals.get('total_seconds')}",
                f"- avg_seconds: {runtime_totals.get('avg_seconds')}",
                f"- max_seconds: {runtime_totals.get('max_seconds')}",
            ]
        )
    runtime_span_summary = summary.get("runtime_span_summary") or {}
    slowest_spans = runtime_span_summary.get("slowest_spans") or []
    if slowest_spans:
        lines.extend(["", "### Slowest spans", ""])
        for span in slowest_spans:
            lines.append(
                f"- {span.get('sample_id')} / {span.get('stage')}: "
                f"{span.get('elapsed_seconds')}s"
                f", provider={span.get('provider') or 'unknown'}"
                f", model={span.get('model') or 'unknown'}"
                f", retries={span.get('retry_count')}"
                f", fallbacks={span.get('fallback_count')}"
            )
    lines.extend(
        [
            "",
            "## Samples",
            "",
            "| sample_id | student_no | classification | answered_questions | total_score_deduction | runtime_total_seconds | paper_review_reasons |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for sample in summary.get("samples", []):
        paper_reasons = "、".join(sample.get("paper_review_reasons") or []) or "无"
        runtime_total = sample.get("runtime_total_seconds")
        lines.append(
            f"| {sample.get('sample_id')} | {sample.get('student_no')} | {sample.get('classification')} | "
            f"{sample.get('answered_questions')} | {sample.get('total_score_deduction')} | "
            f"{'无' if runtime_total is None else runtime_total} | {paper_reasons} |"
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
        runtime_profile = sample.get("runtime_profile") or {}
        stage_seconds = runtime_profile.get("stage_seconds") if isinstance(runtime_profile, dict) else {}
        if isinstance(stage_seconds, dict) and stage_seconds:
            lines.append(
                "- runtime: "
                + ", ".join(f"{stage}={value}" for stage, value in stage_seconds.items())
            )
        diagnostics = sample.get("runtime_diagnostics") or {}
        slowest_span = diagnostics.get("slowest_span") if isinstance(diagnostics, dict) else None
        if isinstance(slowest_span, dict):
            lines.append(
                "- runtime_diagnostics: "
                f"slowest_span={slowest_span.get('stage')}:{slowest_span.get('elapsed_seconds')}s"
                f", provider={slowest_span.get('provider') or 'unknown'}"
                f", retries={slowest_span.get('retry_count')}"
                f", fallbacks={slowest_span.get('fallback_count')}"
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
        smoke_run_dir=str(smoke_result_path.parent),
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
