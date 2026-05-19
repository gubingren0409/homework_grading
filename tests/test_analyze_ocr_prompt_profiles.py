import json
from pathlib import Path

from scripts.analyze_ocr_prompt_profiles import build_ocr_prompt_profile_analysis, render_markdown


def _write_run(path: Path, *, label: str, rows: list[dict]) -> None:
    path.write_text(
        json.dumps({"label": label, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_build_ocr_prompt_profile_analysis_counts_sources_and_review_hits(tmp_path):
    run_path = tmp_path / "short_answer.json"
    _write_run(
        run_path,
        label="short_answer_ocr",
        rows=[
            {
                "student_file": "stu_ans_01.png",
                "answer_text": "A",
                "review_reasons": [],
                "question_review_reasons": ["LOW_QUALITY_CROP"],
                "extraction_warnings": [],
                "runtime_profile": {"stage_seconds": {"total": 10.0}},
                "extraction_debug": {"part_text_sources": {"一/2": "student_tags"}},
            },
            {
                "student_file": "stu_ans_02.png",
                "answer_text": "ABCD",
                "review_reasons": ["ANSWER_EXTRACTION_RISK"],
                "question_review_reasons": [],
                "extraction_warnings": ["ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS"],
                "runtime_profile": {"stage_seconds": {"total": 20.0}},
                "extraction_debug": {"part_text_sources": {"一/2": "ocr_worked_solution_inference"}},
            },
        ],
    )

    analysis = build_ocr_prompt_profile_analysis([run_path])

    assert analysis["profile_count"] == 1
    profile = analysis["profiles"][0]
    assert profile["avg_total_seconds"] == 15.0
    assert profile["worked_solution_fallback_count"] == 1
    assert profile["source_counts"] == {
        "ocr_worked_solution_inference": 1,
        "student_tags": 1,
    }
    assert profile["review_reason_hits"] == {
        "ANSWER_EXTRACTION_RISK": 1,
        "LOW_QUALITY_CROP": 1,
    }


def test_render_markdown_outputs_profile_table():
    markdown = render_markdown(
        {
            "profile_count": 1,
            "profiles": [
                {
                    "label": "worked_solution_ocr",
                    "sample_count": 2,
                    "avg_total_seconds": 15.0,
                    "max_total_seconds": 20.0,
                    "avg_answer_length": 2.5,
                    "worked_solution_fallback_count": 1,
                    "source_counts": {"ocr_worked_solution_inference": 1, "student_tags": 1},
                    "review_reason_hits": {"ANSWER_EXTRACTION_RISK": 1},
                }
            ],
        }
    )

    assert "# OCR prompt profile analysis" in markdown
    assert "| worked_solution_ocr | 2 | 15.0 | 20.0 | 2.5 | 1 |" in markdown
