import json
from pathlib import Path

from scripts.analyze_runtime_spans import build_provider_analysis, render_markdown


def _write_summary(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_build_provider_analysis_groups_and_sorts_tail_sources(tmp_path):
    summary_path = tmp_path / "summary.json"
    _write_summary(
        summary_path,
        {
            "manifest_name": "physics-minimal",
            "samples": [
                {
                    "sample_id": "student-02",
                    "student_no": "02",
                    "runtime_profile": {
                        "spans": [
                            {
                                "stage": "answer_region_ocr",
                                "elapsed_seconds": 12.0,
                                "provider": "qwen",
                                "model": "qwen-vl-plus",
                                "retry_count": 1,
                                "fallback_count": 0,
                                "error_types": ["network"],
                            },
                            {
                                "stage": "cognitive_evaluation",
                                "elapsed_seconds": 55.0,
                                "provider": "deepseek",
                                "model": "deepseek-reasoner",
                                "retry_count": 0,
                                "fallback_count": 1,
                                "error_types": [],
                            },
                        ]
                    },
                },
                {
                    "sample_id": "student-05",
                    "student_no": "05",
                    "runtime_profile": {
                        "spans": [
                            {
                                "stage": "answer_region_ocr",
                                "elapsed_seconds": 20.0,
                                "provider": "qwen",
                                "model": "qwen-vl-plus",
                                "retry_count": 2,
                                "fallback_count": 1,
                                "error_types": ["network", "parse_error"],
                            }
                        ]
                    },
                },
            ],
        },
    )

    analysis = build_provider_analysis([summary_path])

    assert analysis["summary_count"] == 1
    assert analysis["span_count"] == 3
    assert analysis["tail_sources"][0]["stage"] == "cognitive_evaluation"
    assert analysis["tail_sources"][1]["stage"] == "answer_region_ocr"
    assert analysis["tail_sources"][1]["retry_total"] == 3
    assert analysis["tail_sources"][1]["fallback_total"] == 1
    assert analysis["tail_sources"][1]["network_instability_count"] == 2
    assert analysis["error_types"][0] == {
        "stage": "answer_region_ocr",
        "provider": "qwen",
        "model": "qwen-vl-plus",
        "error_type": "network",
        "count": 2,
    }


def test_render_markdown_includes_tail_sources_and_error_types():
    markdown = render_markdown(
        {
            "summary_count": 1,
            "span_count": 2,
            "tail_sources": [
                {
                    "stage": "cognitive_evaluation",
                    "provider": "deepseek",
                    "model": "deepseek-reasoner",
                    "count": 1,
                    "avg_seconds": 55.0,
                    "p95_seconds": 55.0,
                    "p99_seconds": 55.0,
                    "max_seconds": 55.0,
                    "retry_total": 0,
                    "fallback_total": 1,
                    "network_instability_count": 0,
                    "slowest_sample_id": "student-02",
                }
            ],
            "error_types": [
                {
                    "stage": "answer_region_ocr",
                    "provider": "qwen",
                    "model": "qwen-vl-plus",
                    "error_type": "network",
                    "count": 2,
                }
            ],
        }
    )

    assert "# Runtime span analysis" in markdown
    assert "| cognitive_evaluation | deepseek | deepseek-reasoner | 1 | 55.0 | 55.0 | 55.0 | 55.0 | 0 | 1 | 0 | student-02 |" in markdown
    assert "| answer_region_ocr | qwen | qwen-vl-plus | network | 2 |" in markdown
