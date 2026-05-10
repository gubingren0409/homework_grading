import json

from scripts.summarize_real_paper_smoke import render_markdown, summarize_real_paper_smoke


def test_summarize_real_paper_smoke_buckets_pass_review_fail():
    manifest = {
        "manifest_name": "physics-minimal",
        "manifest_version": "1.0",
        "reference_pdf": "ref.pdf",
        "smoke_run_dir": "run-dir",
        "samples": [
            {
                "sample_id": "student-02",
                "student_no": "02",
                "question_ids": ["一/2", "一/5"],
                "expected": {"minimum_answered_questions": 2},
            },
            {
                "sample_id": "student-05",
                "student_no": "05",
                "question_ids": ["一/2", "一/5"],
                "expected": {
                    "minimum_answered_questions": 2,
                    "allowed_paper_review_reasons": ["LOW_QUALITY_CROP"],
                    "allowed_question_review_reasons": ["LOW_QUALITY_CROP"],
                },
            },
            {
                "sample_id": "student-12",
                "student_no": "12",
                "question_ids": ["一/2", "一/5"],
                "expected": {"minimum_answered_questions": 2},
            },
        ],
    }
    smoke_result = {
        "students": [
            {
                "student_no": "02",
                "report": {
                    "answered_questions": 2,
                    "total_score_deduction": 0.0,
                    "review_reasons": [],
                    "per_question": {
                        "一/2": {"status": "SCORED", "review_reasons": [], "total_score_deduction": 0.0},
                        "一/5": {"status": "SCORED", "review_reasons": [], "total_score_deduction": 0.0},
                    },
                },
            },
            {
                "student_no": "05",
                "report": {
                    "answered_questions": 2,
                    "total_score_deduction": 1.0,
                    "review_reasons": ["LOW_QUALITY_CROP"],
                    "per_question": {
                        "一/2": {
                            "status": "SCORED",
                            "review_reasons": ["LOW_QUALITY_CROP"],
                            "total_score_deduction": 1.0,
                        },
                        "一/5": {"status": "SCORED", "review_reasons": [], "total_score_deduction": 0.0},
                    },
                },
            },
            {
                "student_no": "12",
                "report": {
                    "answered_questions": 1,
                    "total_score_deduction": 3.0,
                    "review_reasons": [],
                    "per_question": {
                        "一/2": {"status": "SCORED", "review_reasons": [], "total_score_deduction": 1.0},
                        "一/5": {"status": "REJECTED_UNREADABLE", "review_reasons": [], "total_score_deduction": 2.0},
                    },
                },
            },
        ]
    }

    summary = summarize_real_paper_smoke(manifest, smoke_result)

    assert summary["counts"] == {"pass": 1, "review": 1, "fail": 1}
    assert [sample["classification"] for sample in summary["samples"]] == ["pass", "review", "fail"]
    assert summary["samples"][1]["paper_review_reasons_within_expectation"] is True
    assert "一/5 status=REJECTED_UNREADABLE" in summary["samples"][2]["failure_reasons"]


def test_summarize_real_paper_smoke_fails_unexpected_review_reasons():
    manifest = {
        "samples": [
            {
                "sample_id": "student-02",
                "student_no": "02",
                "question_ids": ["一/2"],
                "expected": {
                    "minimum_answered_questions": 1,
                    "allowed_paper_review_reasons": [],
                    "allowed_question_review_reasons": [],
                },
            }
        ]
    }
    smoke_result = {
        "students": [
            {
                "student_no": "02",
                "report": {
                    "answered_questions": 1,
                    "review_reasons": ["UNEXPECTED_PAPER_REASON"],
                    "per_question": {
                        "一/2": {
                            "status": "SCORED",
                            "review_reasons": ["UNEXPECTED_QUESTION_REASON"],
                            "total_score_deduction": 0.0,
                        }
                    },
                },
            }
        ]
    }

    summary = summarize_real_paper_smoke(manifest, smoke_result)

    assert summary["counts"] == {"pass": 0, "review": 0, "fail": 1}
    failures = summary["samples"][0]["failure_reasons"]
    assert "unexpected paper review_reasons: UNEXPECTED_PAPER_REASON" in failures
    assert "一/2 unexpected review_reasons: UNEXPECTED_QUESTION_REASON" in failures


def test_summarize_real_paper_smoke_can_disallow_review_bucket():
    manifest = {
        "samples": [
            {
                "sample_id": "student-02",
                "student_no": "02",
                "question_ids": ["一/2"],
                "expected": {
                    "minimum_answered_questions": 1,
                    "allowed_question_review_reasons": ["LOW_QUALITY_CROP"],
                    "allow_review_classification": False,
                },
            }
        ]
    }
    smoke_result = {
        "students": [
            {
                "student_no": "02",
                "report": {
                    "answered_questions": 1,
                    "review_reasons": [],
                    "per_question": {
                        "一/2": {
                            "status": "SCORED",
                            "review_reasons": ["LOW_QUALITY_CROP"],
                            "total_score_deduction": 0.0,
                        }
                    },
                },
            }
        ]
    }

    summary = summarize_real_paper_smoke(manifest, smoke_result)

    assert summary["counts"] == {"pass": 0, "review": 0, "fail": 1}
    assert "review classification is not allowed for this sample" in summary["samples"][0]["failure_reasons"]


def test_render_markdown_includes_sample_table():
    summary = {
        "manifest_name": "physics-minimal",
        "manifest_version": "1.0",
        "reference_pdf": "ref.pdf",
        "smoke_run_dir": "run-dir",
        "counts": {"pass": 1, "review": 1, "fail": 0},
        "samples": [
            {
                "sample_id": "student-02",
                "student_no": "02",
                "classification": "pass",
                "answered_questions": 3,
                "total_score_deduction": 0.0,
                "paper_review_reasons": [],
                "question_results": [
                    {"question_id": "四/18", "status": "SCORED", "total_score_deduction": 5.5, "review_reasons": []}
                ],
                "failure_reasons": [],
                "notes": "long answer drift to monitor",
            }
        ],
    }

    markdown = render_markdown(summary)

    assert "# physics-minimal" in markdown
    assert "| student-02 | 02 | pass | 3 | 0.0 | 无 |" in markdown
    assert "- `四/18`: status=SCORED, deduction=5.5, review_reasons=无" in markdown
    assert "- notes: long answer drift to monitor" in markdown
