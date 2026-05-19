from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def build_provider_analysis(summary_paths: list[Path]) -> dict[str, Any]:
    span_records: list[dict[str, Any]] = []
    for path in summary_paths:
        summary = load_summary(path)
        for sample in summary.get("samples", []):
            if not isinstance(sample, dict):
                continue
            runtime_profile = sample.get("runtime_profile")
            spans = runtime_profile.get("spans") if isinstance(runtime_profile, dict) else None
            if not isinstance(spans, list):
                continue
            for span in spans:
                if not isinstance(span, dict):
                    continue
                span_records.append(
                    {
                        "summary_path": str(path),
                        "manifest_name": summary.get("manifest_name"),
                        "sample_id": sample.get("sample_id"),
                        "student_no": sample.get("student_no"),
                        **span,
                    }
                )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in span_records:
        key = (
            str(record.get("stage") or "unknown"),
            str(record.get("provider") or "unknown"),
            str(record.get("model") or "unknown"),
        )
        grouped.setdefault(key, []).append(record)

    rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    for (stage, provider, model), records in grouped.items():
        elapsed_values = [float(record.get("elapsed_seconds") or 0.0) for record in records]
        retry_total = sum(int(record.get("retry_count") or 0) for record in records)
        fallback_total = sum(int(record.get("fallback_count") or 0) for record in records)
        network_count = sum(
            1
            for record in records
            if "network" in {str(item) for item in (record.get("error_types") or [])}
        )
        slowest = max(records, key=lambda record: float(record.get("elapsed_seconds") or 0.0))
        rows.append(
            {
                "stage": stage,
                "provider": provider,
                "model": model,
                "count": len(records),
                "avg_seconds": round(sum(elapsed_values) / len(elapsed_values), 3),
                "p95_seconds": round(_percentile(elapsed_values, 0.95), 3),
                "p99_seconds": round(_percentile(elapsed_values, 0.99), 3),
                "max_seconds": round(max(elapsed_values), 3),
                "retry_total": retry_total,
                "fallback_total": fallback_total,
                "network_instability_count": network_count,
                "slowest_sample_id": slowest.get("sample_id"),
                "slowest_student_no": slowest.get("student_no"),
            }
        )
        error_counts: dict[str, int] = {}
        for record in records:
            for error_type in record.get("error_types") or []:
                normalized = str(error_type or "").strip()
                if normalized:
                    error_counts[normalized] = error_counts.get(normalized, 0) + 1
        for error_type, count in sorted(error_counts.items(), key=lambda item: (-item[1], item[0])):
            error_rows.append(
                {
                    "stage": stage,
                    "provider": provider,
                    "model": model,
                    "error_type": error_type,
                    "count": count,
                }
            )

    rows.sort(
        key=lambda row: (
            -float(row["p95_seconds"]),
            -float(row["max_seconds"]),
            row["stage"],
            row["provider"],
            row["model"],
        )
    )
    error_rows.sort(
        key=lambda row: (
            -int(row["count"]),
            row["stage"],
            row["provider"],
            row["model"],
            row["error_type"],
        )
    )
    return {
        "summary_count": len(summary_paths),
        "span_count": len(span_records),
        "tail_sources": rows,
        "error_types": error_rows,
    }


def render_markdown(analysis: dict[str, Any]) -> str:
    rows = analysis.get("tail_sources") or []
    error_rows = analysis.get("error_types") or []
    lines = [
        "# Runtime span analysis",
        "",
        f"- summary_count: {analysis.get('summary_count', 0)}",
        f"- span_count: {analysis.get('span_count', 0)}",
        "",
        "## Tail sources",
        "",
        "| stage | provider | model | count | avg_seconds | p95_seconds | p99_seconds | max_seconds | retry_total | fallback_total | network_instability_count | slowest_sample_id |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['stage']} | {row['provider']} | {row['model']} | {row['count']} | "
            f"{row['avg_seconds']} | {row['p95_seconds']} | {row['p99_seconds']} | {row['max_seconds']} | "
            f"{row['retry_total']} | {row['fallback_total']} | {row['network_instability_count']} | "
            f"{row['slowest_sample_id'] or 'n/a'} |"
        )
    if error_rows:
        lines.extend(
            [
                "",
                "## Error types",
                "",
                "| stage | provider | model | error_type | count |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in error_rows:
            lines.append(
                f"| {row['stage']} | {row['provider']} | {row['model']} | {row['error_type']} | {row['count']} |"
            )
    return "\n".join(lines).strip() + "\n"


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
    parser = argparse.ArgumentParser(description="Analyze provider/model tail latency from smoke summary span data.")
    parser.add_argument("summary", nargs="+", help="Paths to smoke summary JSON files.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    args = parser.parse_args()

    analysis = build_provider_analysis([Path(item) for item in args.summary])
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
        return
    print(render_markdown(analysis))


if __name__ == "__main__":
    main()
