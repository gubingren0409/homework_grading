from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_runtime_comparison(summary_paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in summary_paths:
        summary = load_summary(path)
        runtime_totals = summary.get("runtime_totals") or {}
        counts = summary.get("counts") or {}
        rows.append(
            {
                "label": path.stem,
                "path": str(path),
                "manifest_name": summary.get("manifest_name"),
                "sample_count": int(runtime_totals.get("sample_count", 0) or 0),
                "total_seconds": float(runtime_totals.get("total_seconds", 0.0) or 0.0),
                "avg_seconds": float(runtime_totals.get("avg_seconds", 0.0) or 0.0),
                "max_seconds": float(runtime_totals.get("max_seconds", 0.0) or 0.0),
                "pass": int(counts.get("pass", 0) or 0),
                "review": int(counts.get("review", 0) or 0),
                "fail": int(counts.get("fail", 0) or 0),
            }
        )
    return sorted(rows, key=lambda row: (row["total_seconds"], row["label"]))


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Smoke runtime comparison",
        "",
        "| label | manifest | samples | total_seconds | avg_seconds | max_seconds | pass | review | fail |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | {row['manifest_name'] or 'n/a'} | {row['sample_count']} | "
            f"{row['total_seconds']} | {row['avg_seconds']} | {row['max_seconds']} | "
            f"{row['pass']} | {row['review']} | {row['fail']} |"
        )
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare runtime totals across smoke summaries.")
    parser.add_argument("summary", nargs="+", help="Paths to real-paper smoke summary JSON files.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    args = parser.parse_args()

    rows = build_runtime_comparison([Path(item) for item in args.summary])
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(render_markdown(rows))


if __name__ == "__main__":
    main()
