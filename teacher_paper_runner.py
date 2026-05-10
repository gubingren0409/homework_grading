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
    lines.extend(["", "## 逐题结果", ""])

    for question_id, question_report in report.per_question.items():
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
        lines.extend(
            [
                "",
                question_report.overall_feedback or "无",
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"


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
