import json
from pathlib import Path

from scripts.compare_smoke_runtime_profiles import build_runtime_comparison, render_markdown


def _write_summary(path: Path, *, name: str, total: float, avg: float, max_value: float, counts: dict[str, int]) -> None:
    path.write_text(
        json.dumps(
            {
                "manifest_name": name,
                "runtime_totals": {
                    "sample_count": 3.0,
                    "total_seconds": total,
                    "avg_seconds": avg,
                    "max_seconds": max_value,
                },
                "counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_build_runtime_comparison_sorts_by_total_seconds(tmp_path):
    slower = tmp_path / "slow.json"
    faster = tmp_path / "fast.json"
    _write_summary(
        slower,
        name="full-run",
        total=42.0,
        avg=14.0,
        max_value=18.0,
        counts={"pass": 2, "review": 1, "fail": 0},
    )
    _write_summary(
        faster,
        name="fast-run",
        total=18.0,
        avg=6.0,
        max_value=8.0,
        counts={"pass": 2, "review": 0, "fail": 1},
    )

    rows = build_runtime_comparison([slower, faster])

    assert [row["label"] for row in rows] == ["fast", "slow"]
    assert rows[0]["pass"] == 2
    assert rows[1]["review"] == 1


def test_render_markdown_includes_runtime_columns():
    markdown = render_markdown(
        [
            {
                "label": "fast",
                "manifest_name": "fast-run",
                "sample_count": 3,
                "total_seconds": 18.0,
                "avg_seconds": 6.0,
                "max_seconds": 8.0,
                "pass": 2,
                "review": 0,
                "fail": 1,
            }
        ]
    )

    assert "| label | manifest | samples | total_seconds | avg_seconds | max_seconds | pass | review | fail |" in markdown
    assert "| fast | fast-run | 3 | 18.0 | 6.0 | 8.0 | 2 | 0 | 1 |" in markdown
