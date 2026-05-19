from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_batch_run(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def build_repro_matrix(run_paths: list[Path]) -> dict[str, Any]:
    grouped_runs: dict[str, list[dict[str, Any]]] = {}
    for path in run_paths:
        payload = load_batch_run(path)
        label = str(payload.get("label") or path.stem)
        grouped_runs.setdefault(label, []).append({"path": str(path), "payload": payload})

    profiles: list[dict[str, Any]] = []
    for label, runs in sorted(grouped_runs.items()):
        student_records: dict[str, list[dict[str, Any]]] = {}
        total_seconds: list[float] = []
        stage_totals: dict[str, list[float]] = {}

        for run in runs:
            payload = run["payload"]
            for row in payload.get("rows", []):
                if not isinstance(row, dict):
                    continue
                student_file = str(row.get("student_file") or "")
                if not student_file:
                    continue
                student_records.setdefault(student_file, []).append(row)
                runtime_profile = row.get("runtime_profile")
                stage_seconds = runtime_profile.get("stage_seconds") if isinstance(runtime_profile, dict) else None
                if isinstance(stage_seconds, dict):
                    for stage, value in stage_seconds.items():
                        if value is None:
                            continue
                        stage_totals.setdefault(str(stage), []).append(float(value))
                    total_value = stage_seconds.get("total")
                    if total_value is not None:
                        total_seconds.append(float(total_value))

        student_count = len(student_records)
        same_deduction_count = 0
        same_review_count = 0
        same_source_count = 0
        inconsistent_students: list[dict[str, Any]] = []
        for student_file, rows in sorted(student_records.items()):
            deductions = {float(row.get("total_score_deduction") or 0.0) for row in rows}
            review_shapes = {
                (
                    bool(row.get("requires_human_review")),
                    tuple(str(reason) for reason in (row.get("review_reasons") or [])),
                    tuple(str(reason) for reason in (row.get("question_review_reasons") or [])),
                )
                for row in rows
            }
            sources = {
                json.dumps(
                    (row.get("extraction_debug") or {}).get("part_text_sources") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for row in rows
            }
            if len(deductions) == 1:
                same_deduction_count += 1
            if len(review_shapes) == 1:
                same_review_count += 1
            if len(sources) == 1:
                same_source_count += 1
            if len(deductions) > 1 or len(review_shapes) > 1 or len(sources) > 1:
                inconsistent_students.append(
                    {
                        "student_file": student_file,
                        "deductions": sorted(deductions),
                        "review_shapes": [list(item) for item in sorted(review_shapes)],
                        "sources": sorted(sources),
                    }
                )

        profiles.append(
            {
                "label": label,
                "run_count": len(runs),
                "student_count": student_count,
                "same_deduction_count": same_deduction_count,
                "same_review_count": same_review_count,
                "same_source_count": same_source_count,
                "same_deduction_ratio": _safe_ratio(same_deduction_count, student_count),
                "same_review_ratio": _safe_ratio(same_review_count, student_count),
                "same_source_ratio": _safe_ratio(same_source_count, student_count),
                "avg_total_seconds": round(sum(total_seconds) / len(total_seconds), 3) if total_seconds else 0.0,
                "p95_total_seconds": round(_percentile(total_seconds, 0.95), 3) if total_seconds else 0.0,
                "max_total_seconds": round(max(total_seconds), 3) if total_seconds else 0.0,
                "over_120_count": sum(1 for value in total_seconds if value > 120.0),
                "stage_stats": {
                    stage: {
                        "count": len(values),
                        "avg_seconds": round(sum(values) / len(values), 3),
                        "p95_seconds": round(_percentile(values, 0.95), 3),
                        "max_seconds": round(max(values), 3),
                    }
                    for stage, values in sorted(stage_totals.items())
                },
                "inconsistent_students": inconsistent_students,
            }
        )

    return {"profile_count": len(profiles), "profiles": profiles}


def render_markdown(matrix: dict[str, Any]) -> str:
    lines = [
        "# Batch repro matrix",
        "",
        f"- profile_count: {matrix.get('profile_count', 0)}",
        "",
        "| label | runs | students | same_deduction_ratio | same_review_ratio | same_source_ratio | avg_total_seconds | p95_total_seconds | max_total_seconds | over_120_count |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for profile in matrix.get("profiles", []):
        lines.append(
            f"| {profile['label']} | {profile['run_count']} | {profile['student_count']} | "
            f"{profile['same_deduction_ratio']} | {profile['same_review_ratio']} | {profile['same_source_ratio']} | "
            f"{profile['avg_total_seconds']} | {profile['p95_total_seconds']} | {profile['max_total_seconds']} | "
            f"{profile['over_120_count']} |"
        )
    for profile in matrix.get("profiles", []):
        if not profile.get("inconsistent_students"):
            continue
        lines.extend(["", f"## {profile['label']} inconsistencies", ""])
        for item in profile["inconsistent_students"]:
            lines.append(
                f"- {item['student_file']}: deductions={item['deductions']}, "
                f"sources={item['sources']}"
            )
    return "\n".join(lines).strip() + "\n"


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 3)


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze repeated batch-run outputs by profile label.")
    parser.add_argument("run", nargs="+", help="Paths to batch run JSON files.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    args = parser.parse_args()

    matrix = build_repro_matrix([Path(item) for item in args.run])
    if args.json:
        print(json.dumps(matrix, ensure_ascii=False, indent=2))
        return
    print(render_markdown(matrix))


if __name__ == "__main__":
    main()
