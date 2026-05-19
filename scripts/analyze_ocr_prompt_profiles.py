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


def build_ocr_prompt_profile_analysis(run_paths: list[Path]) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for path in run_paths:
        payload = load_batch_run(path)
        rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
        source_counts: dict[str, int] = {}
        review_reason_hits: dict[str, int] = {}
        extraction_warning_hits: dict[str, int] = {}
        runtime_totals: list[float] = []
        answer_lengths: list[int] = []
        for row in rows:
            extraction_debug = row.get("extraction_debug") if isinstance(row.get("extraction_debug"), dict) else {}
            part_sources = extraction_debug.get("part_text_sources") if isinstance(extraction_debug, dict) else {}
            if isinstance(part_sources, dict):
                for source in part_sources.values():
                    normalized = str(source or "").strip()
                    if normalized:
                        source_counts[normalized] = source_counts.get(normalized, 0) + 1
            for reason in row.get("review_reasons") or []:
                normalized = str(reason or "").strip()
                if normalized:
                    review_reason_hits[normalized] = review_reason_hits.get(normalized, 0) + 1
            for reason in row.get("question_review_reasons") or []:
                normalized = str(reason or "").strip()
                if normalized:
                    review_reason_hits[normalized] = review_reason_hits.get(normalized, 0) + 1
            for warning in row.get("extraction_warnings") or []:
                normalized = str(warning or "").strip()
                if normalized:
                    extraction_warning_hits[normalized] = extraction_warning_hits.get(normalized, 0) + 1
            runtime_profile = row.get("runtime_profile")
            stage_seconds = runtime_profile.get("stage_seconds") if isinstance(runtime_profile, dict) else None
            if isinstance(stage_seconds, dict) and stage_seconds.get("total") is not None:
                runtime_totals.append(float(stage_seconds.get("total") or 0.0))
            answer_lengths.append(len(str(row.get("answer_text") or "")))
        profiles.append(
            {
                "label": str(payload.get("label") or path.stem),
                "path": str(path),
                "sample_count": len(rows),
                "avg_total_seconds": round(sum(runtime_totals) / len(runtime_totals), 3) if runtime_totals else 0.0,
                "max_total_seconds": round(max(runtime_totals), 3) if runtime_totals else 0.0,
                "avg_answer_length": round(sum(answer_lengths) / len(answer_lengths), 3) if answer_lengths else 0.0,
                "source_counts": dict(sorted(source_counts.items())),
                "review_reason_hits": dict(sorted(review_reason_hits.items())),
                "extraction_warning_hits": dict(sorted(extraction_warning_hits.items())),
                "worked_solution_fallback_count": int(source_counts.get("ocr_worked_solution_inference", 0)),
            }
        )
    return {"profile_count": len(profiles), "profiles": profiles}


def render_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# OCR prompt profile analysis",
        "",
        f"- profile_count: {analysis.get('profile_count', 0)}",
        "",
        "| label | sample_count | avg_total_seconds | max_total_seconds | avg_answer_length | worked_solution_fallback_count | source_counts | review_reason_hits |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for profile in analysis.get("profiles", []):
        lines.append(
            f"| {profile['label']} | {profile['sample_count']} | {profile['avg_total_seconds']} | "
            f"{profile['max_total_seconds']} | {profile['avg_answer_length']} | "
            f"{profile['worked_solution_fallback_count']} | {json.dumps(profile['source_counts'], ensure_ascii=False)} | "
            f"{json.dumps(profile['review_reason_hits'], ensure_ascii=False)} |"
        )
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze OCR prompt-profile outputs from batch repro runs.")
    parser.add_argument("run", nargs="+", help="Batch run JSON paths.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    args = parser.parse_args()

    analysis = build_ocr_prompt_profile_analysis([Path(item) for item in args.run])
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
        return
    print(render_markdown(analysis))


if __name__ == "__main__":
    main()
