import json
from pathlib import Path

from src.schemas.answer_ir import StudentAnswer, StudentAnswerBundle, StudentAnswerPart
from src.schemas.cognitive_ir import EvaluationReport, PaperEvaluationReport
from scripts.run_presegmented_batch_repro import build_batch_row, collect_student_inputs, load_rubric_bundle


def test_load_rubric_bundle_wraps_single_rubric(tmp_path):
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps({"question_id": "一/2", "correct_answer": "A"}, ensure_ascii=False),
        encoding="utf-8",
    )

    bundle = load_rubric_bundle(rubric_path, paper_id="paper-x")

    assert bundle.paper_id == "paper-x"
    assert [rubric.question_id for rubric in bundle.rubrics] == ["一/2"]


def test_collect_student_inputs_supports_files_and_subdirs(tmp_path):
    flat_file = tmp_path / "stu_ans_01.png"
    flat_file.write_bytes(b"flat")
    nested_dir = tmp_path / "stu_ans_02"
    nested_dir.mkdir()
    nested_file = nested_dir / "page_1.png"
    nested_file.write_bytes(b"nested")

    entries = collect_student_inputs(tmp_path)

    assert entries == [
        ("stu_ans_01.png", [flat_file]),
        ("stu_ans_02", [nested_file]),
    ]


def test_build_batch_row_extracts_runtime_and_answer_metadata():
    report = PaperEvaluationReport(
        paper_id="paper-1",
        total_questions=1,
        answered_questions=1,
        total_score_deduction=2.0,
        requires_human_review=True,
        review_reasons=["LOW_QUALITY_CROP"],
        warnings=[],
        runtime_profile={"stage_seconds": {"total": 12.0}},
        student_answer_bundle=StudentAnswerBundle(
            paper_id="paper-1",
            answers=[
                StudentAnswer(
                    question_id="一/2",
                    answer_text="A",
                    ocr_text="A",
                    parts=[
                        StudentAnswerPart(
                            source_question_no="一/2",
                            text="A",
                            answer_text="A",
                            global_confidence=0.95,
                            readability_status="CLEAR",
                        )
                    ],
                    global_confidence=0.95,
                    extraction_warnings=["ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS"],
                    extraction_debug={"part_text_sources": {"一/2": "student_tags"}},
                )
            ],
            question_tree=[],
        ),
        per_question={
            "一/2": EvaluationReport(
                status="SCORED",
                is_fully_correct=False,
                total_score_deduction=2.0,
                step_evaluations=[],
                overall_feedback="需要复核",
                system_confidence=0.6,
                requires_human_review=True,
                review_reasons=["LOW_OCR_CONFIDENCE"],
            )
        },
    )

    row = build_batch_row(student_file="stu_ans_01.png", report=report)

    assert row["student_file"] == "stu_ans_01.png"
    assert row["review_reasons"] == ["LOW_QUALITY_CROP"]
    assert row["question_review_reasons"] == ["LOW_OCR_CONFIDENCE"]
    assert row["answer_text"] == "A"
    assert row["extraction_debug"] == {"part_text_sources": {"一/2": "student_tags"}}
