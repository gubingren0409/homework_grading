from src.schemas.cognitive_ir import EvaluationReport, PaperEvaluationReport
from teacher_paper_runner import _paper_report_to_markdown


def test_paper_report_to_markdown_includes_review_reasons():
    report = PaperEvaluationReport(
        paper_id="paper-1",
        total_questions=2,
        answered_questions=1,
        total_score_deduction=2.0,
        requires_human_review=True,
        review_reasons=["UNMATCHED_REGION_WITHOUT_RUBRIC"],
        warnings=[],
        per_question={
            "1": EvaluationReport(
                status="SCORED",
                is_fully_correct=False,
                total_score_deduction=2.0,
                step_evaluations=[],
                overall_feedback="需要复核。",
                system_confidence=0.62,
                requires_human_review=True,
                review_reasons=["LOW_OCR_CONFIDENCE"],
            )
        },
    )

    markdown = _paper_report_to_markdown(
        student_id="student-1",
        source_names=["paper.pdf"],
        report=report,
    )

    assert "# student-1 整卷批改报告" in markdown
    assert "卷级复核原因：UNMATCHED_REGION_WITHOUT_RUBRIC" in markdown
    assert "### 题号 1" in markdown
    assert "复核原因：LOW_OCR_CONFIDENCE" in markdown
