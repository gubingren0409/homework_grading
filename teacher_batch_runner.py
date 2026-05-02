from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


def _resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


RESOURCE_ROOT = _resource_root()
RUNTIME_ROOT = _runtime_root()

load_dotenv(RUNTIME_ROOT / ".env", override=False)
os.environ.setdefault("PROMPT_INVALIDATION_BUS_ENABLED", "false")

from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.core.config import settings
from src.orchestration.workflow import GradingWorkflow
from src.perception.factory import create_perception_engine
from src.prompts.cache_memory import InMemoryPromptCache
from src.prompts.provider import PromptProviderService
from src.prompts.source_file import FilePromptSource
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.rubric_ir import TeacherRubric
import src.prompts.provider as prompt_provider_module


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
TRIAL_DIR = RUNTIME_ROOT / "teacher_trial"
REFERENCE_DIR = TRIAL_DIR / "reference"
STUDENTS_DIR = TRIAL_DIR / "students"
OUTPUTS_DIR = TRIAL_DIR / "outputs"


@dataclass
class Submission:
    student_id: str
    files_data: list[tuple[bytes, str]]


def _sanitize_name(raw: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", raw.strip(), flags=re.UNICODE)
    value = value.strip("._")
    return value or "student"


def _ensure_workspace() -> None:
    for directory in (TRIAL_DIR, REFERENCE_DIR, STUDENTS_DIR, OUTPUTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _collect_supported_files(directory: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ],
        key=lambda item: item.name.lower(),
    )


def _load_reference_files() -> list[tuple[bytes, str]]:
    files = _collect_supported_files(REFERENCE_DIR)
    if not files:
        raise FileNotFoundError(
            "参考答案文件夹为空。请把标准答案图片或 PDF 放到 teacher_trial\\reference 后重试。"
        )
    return [(path.read_bytes(), path.name) for path in files]


def _load_student_submissions() -> list[Submission]:
    submissions: list[Submission] = []

    for entry in sorted(STUDENTS_DIR.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            submissions.append(
                Submission(
                    student_id=_sanitize_name(entry.stem),
                    files_data=[(entry.read_bytes(), entry.name)],
                )
            )
            continue

        if entry.is_dir():
            files = _collect_supported_files(entry)
            if not files:
                continue
            submissions.append(
                Submission(
                    student_id=_sanitize_name(entry.name),
                    files_data=[(path.read_bytes(), path.name) for path in files],
                )
            )

    if not submissions:
        raise FileNotFoundError(
            "学生作答文件夹为空。请把每位学生的图片/PDF 放到 teacher_trial\\students 后重试。"
        )

    return submissions


def _build_standalone_prompt_provider() -> PromptProviderService:
    prompts_dir = (RESOURCE_ROOT / "configs" / "prompts").resolve()
    if not prompts_dir.exists():
        raise FileNotFoundError(f"未找到 prompts 目录：{prompts_dir}")

    provider = PromptProviderService(
        source=FilePromptSource(base_dir=prompts_dir),
        l1_cache=InMemoryPromptCache(
            ttl_seconds=settings.prompt_l1_ttl_seconds,
            swr_seconds=settings.prompt_l1_swr_seconds,
        ),
        l2_cache=InMemoryPromptCache(
            ttl_seconds=settings.prompt_l2_ttl_seconds,
            swr_seconds=settings.prompt_l2_ttl_seconds,
        ),
        invalidation_bus=None,
        pull_interval_seconds=settings.prompt_pull_interval_seconds,
        l2_ttl_seconds=settings.prompt_l2_ttl_seconds,
        l1_ttl_seconds=settings.prompt_l1_ttl_seconds,
    )
    prompt_provider_module._DEFAULT_PROMPT_PROVIDER = provider
    return provider


def _build_workflow() -> GradingWorkflow:
    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    return GradingWorkflow(perception_engine, cognitive_agent)


def _check_runtime_config() -> None:
    if not settings.parsed_qwen_keys:
        raise RuntimeError("未配置 QWEN_API_KEYS，请在脚本同目录的 .env 文件中填写。")
    if not settings.parsed_deepseek_keys:
        raise RuntimeError("未配置 DEEPSEEK_API_KEYS，请在脚本同目录的 .env 文件中填写。")
    if not settings.llm_egress_enabled:
        raise RuntimeError("当前 LLM_EGRESS_ENABLED=false，已阻断模型外呼。")


def _report_to_markdown(
    *,
    student_id: str,
    source_names: Iterable[str],
    report: EvaluationReport,
    rubric: TeacherRubric,
) -> str:
    lines = [
        f"# {student_id} 批改报告",
        "",
        f"- 来源文件：{', '.join(source_names)}",
        f"- 题目编号：{rubric.question_id}",
        f"- 状态：{report.status}",
        f"- 总扣分：{report.total_score_deduction}",
        f"- 是否完全正确：{'是' if report.is_fully_correct else '否'}",
        f"- 系统置信度：{report.system_confidence:.2f}",
        f"- 是否建议人工复核：{'是' if report.requires_human_review else '否'}",
        "",
        "## 总体评价",
        "",
        report.overall_feedback or "无",
        "",
        "## 扣分点",
        "",
    ]

    wrong_steps = [step for step in report.step_evaluations if not step.is_correct]
    if not wrong_steps:
        lines.append("本次未识别到结构化扣分点。")
    else:
        for index, step in enumerate(wrong_steps, start=1):
            lines.extend(
                [
                    f"### 扣分点 {index}",
                    f"- reference_element_id：{step.reference_element_id}",
                    f"- error_type：{step.error_type or 'NONE'}",
                    f"- correction_suggestion：{step.correction_suggestion or '无'}",
                    "",
                ]
            )

    return "\n".join(lines).strip() + "\n"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run_once() -> Path:
    _ensure_workspace()
    _check_runtime_config()

    provider = _build_standalone_prompt_provider()
    await provider.start()
    try:
        reference_files = _load_reference_files()
        submissions = _load_student_submissions()
        workflow = _build_workflow()

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = OUTPUTS_DIR / f"run_{run_id}"
        reports_dir = run_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        print(">>> 正在生成评分标准...")
        rubric = await workflow.generate_rubric_pipeline(reference_files)
        _write_json(run_dir / "rubric.json", rubric.model_dump())

        summary_rows: list[dict[str, object]] = []
        batch_overview: list[dict[str, object]] = []

        for index, submission in enumerate(submissions, start=1):
            print(f">>> [{index}/{len(submissions)}] 正在批改：{submission.student_id}")
            report, perception_snapshot, cognitive_snapshot = await workflow.run_pipeline_with_snapshots(
                submission.files_data,
                rubric=rubric,
            )

            student_dir = reports_dir / submission.student_id
            student_dir.mkdir(parents=True, exist_ok=True)

            payload = {
                "student_id": submission.student_id,
                "source_files": [name for _, name in submission.files_data],
                "rubric": rubric.model_dump(),
                "evaluation_report": report.model_dump(),
                "perception_output": perception_snapshot,
                "cognitive_snapshot": cognitive_snapshot,
            }

            _write_json(student_dir / "report.json", payload)
            (student_dir / "report.md").write_text(
                _report_to_markdown(
                    student_id=submission.student_id,
                    source_names=[name for _, name in submission.files_data],
                    report=report,
                    rubric=rubric,
                ),
                encoding="utf-8",
            )

            summary_row = {
                "student_id": submission.student_id,
                "status": report.status,
                "total_deduction": report.total_score_deduction,
                "is_fully_correct": report.is_fully_correct,
                "system_confidence": round(report.system_confidence, 4),
                "requires_human_review": report.requires_human_review,
                "source_files": "; ".join(name for _, name in submission.files_data),
                "report_json": str((student_dir / "report.json").relative_to(run_dir)),
                "report_markdown": str((student_dir / "report.md").relative_to(run_dir)),
            }
            summary_rows.append(summary_row)
            batch_overview.append(summary_row)

        with (run_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "student_id",
                    "status",
                    "total_deduction",
                    "is_fully_correct",
                    "system_confidence",
                    "requires_human_review",
                    "source_files",
                    "report_json",
                    "report_markdown",
                ],
            )
            writer.writeheader()
            writer.writerows(summary_rows)

        batch_report_lines = [
            "# 批量批改汇总",
            "",
            f"- 运行目录：{run_dir}",
            f"- 感知层 provider：{settings.perception_provider}",
            f"- 感知模型：{settings.qwen_model_name}",
            f"- 认知模型：{settings.deepseek_model_name}",
            f"- 参考答案文件数：{len(reference_files)}",
            f"- 学生样本数：{len(submissions)}",
            "",
            "## 评分标准摘要",
            "",
            f"- question_id：{rubric.question_id}",
            f"- 评分点数量：{len(rubric.grading_points)}",
            "",
            "## 学生结果",
            "",
        ]

        for row in batch_overview:
            batch_report_lines.append(
                f"- {row['student_id']}｜状态={row['status']}｜扣分={row['total_deduction']}｜"
                f"置信度={row['system_confidence']}｜人工复核={row['requires_human_review']}"
            )

        (run_dir / "batch_report.md").write_text(
            "\n".join(batch_report_lines).strip() + "\n",
            encoding="utf-8",
        )

        return run_dir
    finally:
        await provider.stop()


def main() -> int:
    print("=== 教师试用版批量批改脚本 ===")
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
    print("批改完成。")
    print(f"结果目录：{run_dir}")
    print(f"请优先查看：{run_dir / 'summary.csv'}")
    print(f"以及：{run_dir / 'batch_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
