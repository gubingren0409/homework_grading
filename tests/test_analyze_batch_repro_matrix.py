import json
from pathlib import Path

from scripts.analyze_batch_repro_matrix import build_repro_matrix, render_markdown


def _write_run(path: Path, *, label: str, rows: list[dict]) -> None:
    path.write_text(
        json.dumps({"label": label, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_build_repro_matrix_groups_by_label_and_detects_inconsistency(tmp_path):
    full_round_1 = tmp_path / "full_round_1.json"
    full_round_2 = tmp_path / "full_round_2.json"
    fast_round_1 = tmp_path / "fast_round_1.json"
    _write_run(
        full_round_1,
        label="full",
        rows=[
            {
                "student_file": "stu_ans_01.png",
                "total_score_deduction": 0.0,
                "requires_human_review": False,
                "review_reasons": [],
                "question_review_reasons": [],
                "runtime_profile": {"stage_seconds": {"total": 100.0, "answer_region_ocr": 20.0}},
                "extraction_debug": {"part_text_sources": {"一/2": "student_tags"}},
            },
            {
                "student_file": "stu_ans_02.png",
                "total_score_deduction": 1.0,
                "requires_human_review": True,
                "review_reasons": ["LOW_QUALITY_CROP"],
                "question_review_reasons": ["LOW_QUALITY_CROP"],
                "runtime_profile": {"stage_seconds": {"total": 140.0, "cognitive_evaluation": 80.0}},
                "extraction_debug": {"part_text_sources": {"一/2": "student_tags"}},
            },
        ],
    )
    _write_run(
        full_round_2,
        label="full",
        rows=[
            {
                "student_file": "stu_ans_01.png",
                "total_score_deduction": 0.0,
                "requires_human_review": False,
                "review_reasons": [],
                "question_review_reasons": [],
                "runtime_profile": {"stage_seconds": {"total": 90.0, "answer_region_ocr": 18.0}},
                "extraction_debug": {"part_text_sources": {"一/2": "student_tags"}},
            },
            {
                "student_file": "stu_ans_02.png",
                "total_score_deduction": 2.0,
                "requires_human_review": True,
                "review_reasons": ["LOW_QUALITY_CROP"],
                "question_review_reasons": ["LOW_QUALITY_CROP"],
                "runtime_profile": {"stage_seconds": {"total": 150.0, "cognitive_evaluation": 90.0}},
                "extraction_debug": {"part_text_sources": {"一/2": "ocr_worked_solution_inference"}},
            },
        ],
    )
    _write_run(
        fast_round_1,
        label="fast",
        rows=[
            {
                "student_file": "stu_ans_01.png",
                "total_score_deduction": 0.0,
                "requires_human_review": False,
                "review_reasons": [],
                "question_review_reasons": [],
                "runtime_profile": {"stage_seconds": {"total": 60.0, "answer_region_ocr": 12.0}},
                "extraction_debug": {"part_text_sources": {"一/2": "student_tags"}},
            }
        ],
    )

    matrix = build_repro_matrix([full_round_1, full_round_2, fast_round_1])

    assert matrix["profile_count"] == 2
    assert matrix["profiles"][0]["label"] == "fast"
    full_profile = next(profile for profile in matrix["profiles"] if profile["label"] == "full")
    assert full_profile["run_count"] == 2
    assert full_profile["student_count"] == 2
    assert full_profile["same_deduction_count"] == 1
    assert full_profile["same_source_count"] == 1
    assert full_profile["over_120_count"] == 2
    assert full_profile["inconsistent_students"][0]["student_file"] == "stu_ans_02.png"


def test_render_markdown_outputs_profile_table():
    markdown = render_markdown(
        {
            "profile_count": 1,
            "profiles": [
                {
                    "label": "full",
                    "run_count": 2,
                    "student_count": 2,
                    "same_deduction_ratio": 0.5,
                    "same_review_ratio": 1.0,
                    "same_source_ratio": 0.5,
                    "avg_total_seconds": 120.0,
                    "p95_total_seconds": 149.5,
                    "max_total_seconds": 150.0,
                    "over_120_count": 2,
                    "inconsistent_students": [
                        {
                            "student_file": "stu_ans_02.png",
                            "deductions": [1.0, 2.0],
                            "sources": ['{"一/2": "ocr_worked_solution_inference"}', '{"一/2": "student_tags"}'],
                        }
                    ],
                }
            ],
        }
    )

    assert "# Batch repro matrix" in markdown
    assert "| full | 2 | 2 | 0.5 | 1.0 | 0.5 | 120.0 | 149.5 | 150.0 | 2 |" in markdown
    assert "- stu_ans_02.png: deductions=[1.0, 2.0]" in markdown
