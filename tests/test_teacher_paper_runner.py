from src.schemas.answer_ir import StudentAnswer, StudentAnswerBundle, StudentAnswerPart
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
        runtime_profile={
            "stage_seconds": {
                "total": 12.345,
                "student_page_ocr": 1.234,
                "student_page_layout": 0.456,
                "split": 0.123,
                "answer_region_preprocess": 0.789,
                "answer_region_ocr": 4.567,
                "cognitive_evaluation": 3.21,
                "artifact_write": 0.654,
            },
            "spans": [
                {
                    "stage": "cognitive_evaluation",
                    "elapsed_seconds": 3.21,
                    "provider": "deepseek",
                    "retry_count": 1,
                    "fallback_count": 0,
                    "item_refs": [{"question_id": "1"}],
                }
            ],
        },
        student_answer_bundle=StudentAnswerBundle(
            paper_id="paper-1",
            answers=[
                StudentAnswer(
                    question_id="1",
                    answer_text="【来源题号】1\nT=2s",
                    ocr_text="【来源题号】1\n18. 解:(1) T=2s",
                    parts=[
                        StudentAnswerPart(
                            source_question_no="1",
                            crop_path="file:///E:/ai%E6%89%B9%E6%94%B9/data/uploads/paper_crops/q1.png",
                            text="18. 解:(1) T=2s",
                            answer_text="T=2s",
                            global_confidence=0.95,
                            readability_status="CLEAR",
                            extraction_warnings=["ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS"],
                            extraction_debug={
                                "text_source": "ocr_worked_solution_inference",
                                "focus_decision": "full_width_baseline_retained",
                                "filter_reasons": ["STRIPPED_QUESTION_PREFIX"],
                            },
                        )
                    ],
                    global_confidence=0.95,
                    extraction_warnings=["ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS"],
                    extraction_debug={
                        "focus_decision": "full_width_baseline_retained",
                        "part_text_sources": {"1": "ocr_worked_solution_inference"},
                        "filter_reasons": ["STRIPPED_QUESTION_PREFIX"],
                    },
                )
            ],
            question_tree=[],
        ),
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
    assert "## 运行时概览" in markdown
    assert "总耗时（s）：12.345" in markdown
    assert "作答区 OCR（s）：4.567" in markdown
    assert "### 慢调用摘要" in markdown
    assert "cognitive_evaluation: 3.210s, provider=deepseek, retries=1, fallbacks=0 question=1" in markdown
    assert "### 题号 1" in markdown
    assert "复核原因：LOW_OCR_CONFIDENCE" in markdown
    assert "提取来源：1=ocr_worked_solution_inference" in markdown
    assert "提取过滤：STRIPPED_QUESTION_PREFIX" in markdown
    assert "提取告警：ANSWER_TEXT_INFERRED_FROM_OCR_WITHOUT_STUDENT_TAGS" in markdown
    assert "crop 回看：file:///E:/ai%E6%89%B9%E6%94%B9/data/uploads/paper_crops/q1.png" in markdown
