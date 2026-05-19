from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from typing import Iterable

import fitz

from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.paper_workflow import PaperGradingWorkflow
from src.orchestration.rubric_bundle_workflow import RubricBundleWorkflow
from src.perception.factory import create_perception_engine
from src.skills.service import SkillService
from src.utils.file_parsers import process_multiple_files
from teacher_batch_runner import (
    OUTPUTS_DIR,
    REFERENCE_DIR,
    RUNTIME_ROOT,
    STUDENTS_DIR,
    _build_standalone_prompt_provider,
    _check_runtime_config,
    _ensure_workspace,
    _load_reference_files,
    _load_student_submissions,
    _write_json,
)


def _extract_embedded_pdf_text(files_data: list[tuple[bytes, str]]) -> str | None:
    if not files_data or any(not filename.lower().endswith(".pdf") for _, filename in files_data):
        return None

    pages: list[str] = []
    for content, _ in files_data:
        with fitz.open(stream=content, filetype="pdf") as document:
            pages.extend(page.get_text("text") for page in document)
    text = "\n".join(page.strip() for page in pages if page.strip()).strip()
    if len(text) < 200 or "答案" not in text:
        return None
    return text


def _paper_report_to_markdown(
    *,
    student_id: str,
    source_names: Iterable[str],
    report: object,
) -> str:
    from src.schemas.cognitive_ir import PaperEvaluationReport

    if not isinstance(report, PaperEvaluationReport):
        raise TypeError("report must be a PaperEvaluationReport")

    lines = [
        f"# {student_id} 整卷批改报告",
        "",
        f"- 来源文件：{', '.join(source_names)}",
        f"- paper_id：{report.paper_id}",
        f"- 识别题数：{report.answered_questions}/{report.total_questions}",
        f"- 总扣分：{report.total_score_deduction}",
        f"- 是否建议人工复核：{'是' if report.requires_human_review else '否'}",
    ]
    if report.review_reasons:
        lines.append(f"- 卷级复核原因：{'；'.join(report.review_reasons)}")
    stage_seconds = (
        report.runtime_profile.get("stage_seconds")
        if isinstance(report.runtime_profile, dict)
        else None
    )
    if isinstance(stage_seconds, dict) and stage_seconds:
        lines.extend(
            [
                "",
                "## 运行时概览",
                "",
                f"- 总耗时（s）：{float(stage_seconds.get('total') or 0.0):.3f}",
                f"- 页面 OCR（s）：{float(stage_seconds.get('student_page_ocr') or 0.0):.3f}",
                f"- 页面 layout（s）：{float(stage_seconds.get('student_page_layout') or 0.0):.3f}",
                f"- 切题（s）：{float(stage_seconds.get('split') or 0.0):.3f}",
                f"- 作答区预处理（s）：{float(stage_seconds.get('answer_region_preprocess') or 0.0):.3f}",
                f"- 作答区 OCR（s）：{float(stage_seconds.get('answer_region_ocr') or 0.0):.3f}",
                f"- 认知评分（s）：{float(stage_seconds.get('cognitive_evaluation') or 0.0):.3f}",
                f"- 产物写盘（s）：{float(stage_seconds.get('artifact_write') or 0.0):.3f}",
            ]
        )
    spans = (
        report.runtime_profile.get("spans")
        if isinstance(report.runtime_profile, dict)
        else None
    )
    if isinstance(spans, list) and spans:
        slowest_spans = sorted(
            (
                span for span in spans
                if isinstance(span, dict) and span.get("elapsed_seconds") is not None
            ),
            key=lambda span: float(span.get("elapsed_seconds") or 0.0),
            reverse=True,
        )[:3]
        if slowest_spans:
            lines.extend(["", "### 慢调用摘要", ""])
            for span in slowest_spans:
                item_refs = span.get("item_refs") or []
                item_label = ""
                if item_refs:
                    first_ref = item_refs[0]
                    if isinstance(first_ref, dict):
                        if first_ref.get("question_id"):
                            item_label = f" question={first_ref.get('question_id')}"
                        elif first_ref.get("page_index") is not None:
                            item_label = f" page={first_ref.get('page_index')}"
                lines.append(
                    f"- {span.get('stage')}: {float(span.get('elapsed_seconds') or 0.0):.3f}s"
                    f", provider={span.get('provider') or 'unknown'}"
                    f", retries={int(span.get('retry_count') or 0)}"
                    f", fallbacks={int(span.get('fallback_count') or 0)}"
                    f"{item_label}"
                )
    lines.extend(["", "## 逐题结果", ""])
    answers_by_question = _answers_by_question(report)

    for question_id, question_report in report.per_question.items():
        answer = answers_by_question.get(question_id)
        lines.extend(
            [
                f"### 题号 {question_id}",
                f"- 状态：{question_report.status}",
                f"- 扣分：{question_report.total_score_deduction}",
                f"- 置信度：{question_report.system_confidence:.2f}",
                f"- 是否建议人工复核：{'是' if question_report.requires_human_review else '否'}",
            ]
        )
        if question_report.review_reasons:
            lines.append(f"- 复核原因：{'；'.join(question_report.review_reasons)}")
        if answer is not None:
            part_text_sources = answer.extraction_debug.get("part_text_sources", {})
            if isinstance(part_text_sources, dict) and part_text_sources:
                lines.append(
                    "- 提取来源："
                    + "；".join(f"{part}={source}" for part, source in part_text_sources.items())
                )
            filter_reasons = answer.extraction_debug.get("filter_reasons", [])
            if isinstance(filter_reasons, list) and filter_reasons:
                lines.append("- 提取过滤：" + "；".join(str(reason) for reason in filter_reasons))
            if answer.extraction_warnings:
                lines.append("- 提取告警：" + "；".join(answer.extraction_warnings))
            crop_paths = [part.crop_path for part in answer.parts if part.crop_path]
            if crop_paths:
                lines.append("- crop 回看：" + "；".join(crop_paths))
        lines.extend(
            [
                "",
                question_report.overall_feedback or "无",
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def _answers_by_question(report: object) -> dict[str, object]:
    from src.schemas.cognitive_ir import PaperEvaluationReport

    if not isinstance(report, PaperEvaluationReport) or report.student_answer_bundle is None:
        return {}
    return {
        answer.question_id: answer
        for answer in report.student_answer_bundle.answers
    }


def _build_paper_workflows() -> tuple[RubricBundleWorkflow, PaperGradingWorkflow]:
    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    skill_service = SkillService()
    rubric_workflow = RubricBundleWorkflow(
        perception_engine=perception_engine,
        skill_service=skill_service,
        cognitive_agent=cognitive_agent,
    )
    paper_workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
        skill_service=skill_service,
    )
    return rubric_workflow, paper_workflow


async def _generate_rubric_bundle(
    workflow: RubricBundleWorkflow,
    reference_files: list[tuple[bytes, str]],
    *,
    paper_id: str,
):
    embedded_pdf_text = _extract_embedded_pdf_text(reference_files)
    if embedded_pdf_text:
        return await workflow.generate_from_printed_reference_text(
            embedded_pdf_text,
            paper_id=paper_id,
        )
    image_bytes_list = await process_multiple_files(reference_files)
    return await workflow.generate_from_printed_reference(
        image_bytes_list,
        paper_id=paper_id,
    )


async def _run_once():
    _ensure_workspace()
    _check_runtime_config()

    provider = _build_standalone_prompt_provider()
    await provider.start()
    try:
        reference_files = _load_reference_files()
        submissions = _load_student_submissions()
        rubric_workflow, paper_workflow = _build_paper_workflows()

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = OUTPUTS_DIR / f"paper_run_{run_id}"
        reports_dir = run_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        paper_id = f"teacher-paper-{run_id}"

        print(">>> 正在生成整卷评分标准...")
        rubric_bundle = await _generate_rubric_bundle(
            rubric_workflow,
            reference_files,
            paper_id=paper_id,
        )
        _write_json(run_dir / "rubric_bundle.json", rubric_bundle.model_dump())

        summary_rows: list[dict[str, object]] = []
        batch_report_lines = [
            "# 整卷批改汇总",
            "",
            f"- 运行目录：{run_dir}",
            f"- 参考答案文件数：{len(reference_files)}",
            f"- 学生样本数：{len(submissions)}",
            f"- 题目数：{len(rubric_bundle.rubrics)}",
            "",
            "## 学生结果",
            "",
        ]

        for index, submission in enumerate(submissions, start=1):
            print(f">>> [{index}/{len(submissions)}] 正在批改整卷：{submission.student_id}")
            report = await paper_workflow.run_pipeline(submission.files_data, rubric_bundle)

            student_dir = reports_dir / submission.student_id
            student_dir.mkdir(parents=True, exist_ok=True)

            payload = {
                "student_id": submission.student_id,
                "source_files": [name for _, name in submission.files_data],
                "rubric_bundle": rubric_bundle.model_dump(),
                "paper_evaluation_report": report.model_dump(),
            }
            _write_json(student_dir / "report.json", payload)
            (student_dir / "report.md").write_text(
                _paper_report_to_markdown(
                    student_id=submission.student_id,
                    source_names=[name for _, name in submission.files_data],
                    report=report,
                ),
                encoding="utf-8",
            )

            summary_row = {
                "student_id": submission.student_id,
                "paper_id": report.paper_id,
                "answered_questions": report.answered_questions,
                "total_questions": report.total_questions,
                "total_deduction": report.total_score_deduction,
                "runtime_total_seconds": (
                    float(report.runtime_profile.get("stage_seconds", {}).get("total") or 0.0)
                    if isinstance(report.runtime_profile, dict)
                    else 0.0
                ),
                "runtime_answer_region_ocr_seconds": (
                    float(report.runtime_profile.get("stage_seconds", {}).get("answer_region_ocr") or 0.0)
                    if isinstance(report.runtime_profile, dict)
                    else 0.0
                ),
                "runtime_cognitive_seconds": (
                    float(report.runtime_profile.get("stage_seconds", {}).get("cognitive_evaluation") or 0.0)
                    if isinstance(report.runtime_profile, dict)
                    else 0.0
                ),
                "requires_human_review": report.requires_human_review,
                "review_reasons": "；".join(report.review_reasons),
                "source_files": "; ".join(name for _, name in submission.files_data),
                "report_json": str((student_dir / "report.json").relative_to(run_dir)),
                "report_markdown": str((student_dir / "report.md").relative_to(run_dir)),
            }
            summary_rows.append(summary_row)
            batch_report_lines.append(
                f"- {submission.student_id}｜识别={report.answered_questions}/{report.total_questions}"
                f"｜总扣分={report.total_score_deduction}"
                f"｜总耗时={float(report.runtime_profile.get('stage_seconds', {}).get('total') or 0.0):.3f}s"
                f"｜人工复核={report.requires_human_review}"
                f"{'｜原因=' + '、'.join(report.review_reasons) if report.review_reasons else ''}"
            )

        with (run_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "student_id",
                    "paper_id",
                    "answered_questions",
                    "total_questions",
                    "total_deduction",
                    "runtime_total_seconds",
                    "runtime_answer_region_ocr_seconds",
                    "runtime_cognitive_seconds",
                    "requires_human_review",
                    "review_reasons",
                    "source_files",
                    "report_json",
                    "report_markdown",
                ],
            )
            writer.writeheader()
            writer.writerows(summary_rows)

        (run_dir / "batch_report.md").write_text(
            "\n".join(batch_report_lines).strip() + "\n",
            encoding="utf-8",
        )

        return run_dir
    finally:
        await provider.stop()


def main() -> int:
    print("=== 教师试用版整卷批改脚本 ===")
    print(f"运行目录：{RUNTIME_ROOT}")
    print(f"参考答案目录：{REFERENCE_DIR}")
    print(f"学生作答目录：{STUDENTS_DIR}")
    print(f"输出目录：{OUTPUTS_DIR}")
    print("")

    try:
        run_dir = asyncio.run(_run_once())
    except Exception as exc:
        print("")
        print("脚本执行失败：")
        print(str(exc))
        return 1

    print("")
    print("整卷批改完成。")
    print(f"结果目录：{run_dir}")
    print(f"请优先查看：{run_dir / 'summary.csv'}")
    print(f"以及：{run_dir / 'batch_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
