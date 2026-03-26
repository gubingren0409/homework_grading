import argparse
import asyncio
import csv
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.core.exceptions import PerceptionShortCircuitError
from src.db.client import init_db, insert_grading_results
from src.orchestration.workflow import GradingWorkflow
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import PerceptionOutput
from src.schemas.rubric_ir import TeacherRubric
from src.utils.file_parsers import process_multiple_files


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def _infer_question_id_from_students_dir(students_dir: Path) -> str | None:
    """
    尝试从 students_dir 推断 question_id（例如 .../question_02/students）。
    推断失败时返回 None，后续回退到 rubric.question_id。
    """
    candidates = [students_dir.name, students_dir.parent.name]
    for candidate in candidates:
        if candidate.startswith("question_"):
            return candidate
    return None


def _merge_readability_status(statuses: Sequence[str]) -> str:
    """
    将多页可读性状态合并为一个最保守结论。
    """
    if not statuses:
        return "UNREADABLE"
    priority = {"UNREADABLE": 0, "HEAVILY_ALTERED": 1, "MINOR_ALTERATION": 2, "CLEAR": 3}
    return min(statuses, key=lambda s: priority.get(s, -1))


async def _build_merged_perception_output(
    workflow: GradingWorkflow,
    files_data: List[Tuple[bytes, str]],
) -> PerceptionOutput:
    """
    白盒复用工作流内部引擎，生成多文件聚合后的 PerceptionOutput。
    目的：在不改 API 的前提下，把感知层结果与认知层结果一并落盘。
    """
    image_bytes_list = await process_multiple_files(files_data)
    all_elements = []
    confidences: List[float] = []
    readability_statuses: List[str] = []
    all_pages_blank = True

    for page_idx, page_bytes in enumerate(image_bytes_list):
        page_ir = await workflow._perception_engine.process_image(page_bytes)  # 白盒审查阶段允许内部访问
        readability_statuses.append(page_ir.readability_status)
        confidences.append(page_ir.global_confidence)

        if not page_ir.is_blank:
            all_pages_blank = False

        # Phase 27.1: 分层防御 - 仅硬拦截完全不可读的图像
        # UNREADABLE: 无法提取任何信息（全黑、纯噪点）→ 硬拦截
        # HEAVILY_ALTERED: 可提取但质量差（涂改、模糊）→ 放行到认知层判断
        if page_ir.trigger_short_circuit or page_ir.readability_status == "UNREADABLE":
            raise PerceptionShortCircuitError(
                readability_status=page_ir.readability_status,
                message=f"Workflow halted on page {page_idx}: Image quality too poor.",
            )
        
        # 对严重涂改的图像记录警告但放行
        if page_ir.readability_status == "HEAVILY_ALTERED":
            logger.warning(
                f"Page {page_idx} has heavily altered content (confidence: {page_ir.global_confidence:.2f}). "
                "Forwarding to cognitive layer for final judgment."
            )

        for elem in page_ir.elements:
            elem.element_id = f"p{page_idx}_{elem.element_id}"
            all_elements.append(elem)

    merged_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return PerceptionOutput(
        readability_status=_merge_readability_status(readability_statuses),
        elements=all_elements,
        global_confidence=merged_confidence,
        is_blank=all_pages_blank,
        trigger_short_circuit=False,
    )


async def _evaluate_with_full_outputs(
    workflow: GradingWorkflow,
    files_data: List[Tuple[bytes, str]],
    rubric: TeacherRubric,
) -> Tuple[PerceptionOutput, EvaluationReport]:
    """
    产出全量中间与最终结果：
    - PerceptionOutput: 用于审查台可视化
    - EvaluationReport: 用于业务评分与决策
    """
    perception_output = await _build_merged_perception_output(workflow, files_data)
    
    # Phase 26: Blank Page Short-circuit
    if perception_output.is_blank:
        report = EvaluationReport(
            is_fully_correct=False,
            total_score_deduction=0.0,
            step_evaluations=[],
            overall_feedback="试卷未作答（检测到空白卷或无手写作答痕迹）。",
            system_confidence=1.0,
            requires_human_review=False
        )
        return perception_output, report

    report: EvaluationReport = await workflow._cognitive_agent.evaluate_logic(  # 白盒审查阶段允许内部访问
        perception_output,
        rubric=rubric,
    )
    return perception_output, report


async def process_single_student(
    student_id: str,
    file_paths: List[Path],
    rubric: TeacherRubric,
    resolved_question_id: str,
    semaphore: asyncio.Semaphore,
    workflow: GradingWorkflow,
    output_dir: Path,
) -> Dict[str, Any]:
    """
    异步处理单个学生样本：
    1) 先产出 PerceptionOutput
    2) 再产出 EvaluationReport
    3) 返回可用于 DB 批量写入的结构化对象
    """
    async with semaphore:
        logger.info("Processing student: %s", student_id)
        try:
            files_data: List[Tuple[bytes, str]] = [(p.read_bytes(), p.name) for p in file_paths]
            perception_output, report = await _evaluate_with_full_outputs(workflow, files_data, rubric)

            # Phase 27: 状态机拦截 - 检测拒绝状态
            if report.status == "REJECTED_UNREADABLE":
                logger.warning(
                    "Task rejected by Cognitive Engine for student %s: Unreadable or invalid input.",
                    student_id
                )

            output_payload = {
                "student_id": student_id,
                "question_id": resolved_question_id,
                "perception_output": perception_output.model_dump(),
                "evaluation_report": report.model_dump(),
            }
            report_file = output_dir / f"{student_id}.json"
            report_file.write_text(
                EvaluationReport.model_validate(report).model_dump_json(indent=2),
                encoding="utf-8",
            )
            (output_dir / f"{student_id}_full.json").write_text(
                json.dumps(output_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            return {
                "Student_ID": student_id,
                "Total_Deduction": report.total_score_deduction,
                "Is_Fully_Correct": report.is_fully_correct,
                "Requires_Human_Review": report.requires_human_review,
                "Error_Status": "NONE",
                "Status": report.status,  # 新增状态字段
                "Raw_Perception": perception_output,
                "Raw_Report": report,
            }
        except Exception as exc:
            logger.error("Failed to process student %s: %s", student_id, exc)
            return {
                "Student_ID": student_id,
                "Total_Deduction": 0.0,
                "Is_Fully_Correct": False,
                "Requires_Human_Review": True,
                "Error_Status": str(exc),
                "Status": "ERROR",  # 异常情况标记
                "Raw_Perception": None,
                "Raw_Report": None,
            }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Batch Asynchronous Homework Grader.")
    parser.add_argument("--students_dir", type=str, required=True, help="Root directory for student submissions.")
    parser.add_argument("--rubric_file", type=str, required=True, help="Path to the TeacherRubric JSON.")
    parser.add_argument("--output_dir", type=str, required=True, help="Directory to save output reports.")
    parser.add_argument("--db_path", type=str, default="outputs/grading_database.db", help="Path to the SQLite database.")
    parser.add_argument("--concurrency", type=int, default=2, help="Maximum concurrent grading tasks.")
    parser.add_argument(
        "--question_id",
        type=str,
        default=None,
        help="可选：显式指定写入数据库的 question_id；未提供时自动从 students_dir 推断，失败则回退 rubric.question_id。",
    )
    args = parser.parse_args()

    students_dir = Path(args.students_dir)
    output_dir = Path(args.output_dir)
    rubric_path = Path(args.rubric_file)
    db_path = args.db_path
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        await init_db(db_path)
        logger.info("Database initialized at: %s", db_path)
    except Exception as exc:
        logger.critical("Database initialization failed: %s", exc)
        return

    try:
        rubric = TeacherRubric.model_validate_json(rubric_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.critical("Failed to load rubric: %s", exc)
        return

    resolved_question_id = (
        args.question_id
        or _infer_question_id_from_students_dir(students_dir)
        or rubric.question_id
    )

    student_map: Dict[str, List[Path]] = {}
    extensions = {".jpg", ".jpeg", ".png", ".pdf"}
    for item in students_dir.iterdir():
        if item.is_dir():
            student_id = item.name
            files = [f for f in item.iterdir() if f.suffix.lower() in extensions]
            if files:
                student_map[student_id] = sorted(files)
        elif item.is_file() and item.suffix.lower() in extensions:
            student_map[item.stem] = [item]

    if not student_map:
        logger.warning("No student submissions found in %s", args.students_dir)
        return

    logger.info(
        "Discovered %s students. Starting batch grading (concurrency=%s)...",
        len(student_map),
        args.concurrency,
    )

    perception_engine = QwenVLMPerceptionEngine()
    cognitive_agent = DeepSeekCognitiveEngine()
    workflow = GradingWorkflow(perception_engine, cognitive_agent)
    semaphore = asyncio.Semaphore(args.concurrency)

    tasks = []
    for student_id, file_paths in student_map.items():
        # Incremental Rerun Logic (Phase 16.5)
        full_report_path = output_dir / f"{student_id}_full.json"
        if full_report_path.exists():
            try:
                existing_data = json.loads(full_report_path.read_text(encoding="utf-8"))
                # Check if it was a successful run previously
                if existing_data.get("evaluation_report") and not existing_data.get("error_status"):
                    logger.info("Skipping student %s (already processed successfully).", student_id)
                    continue
            except Exception:
                pass # If file is corrupt, re-process
        
        tasks.append(
            process_single_student(
                student_id,
                file_paths,
                rubric,
                resolved_question_id,
                semaphore,
                workflow,
                output_dir,
            )
        )
    
    if not tasks:
        logger.info("All students in this directory have been successfully processed. Nothing to do.")
        return

    results = await asyncio.gather(*tasks)

    batch_task_id = f"batch-{resolved_question_id}-{uuid.uuid4().hex[:8]}"
    db_payload: List[Dict[str, Any]] = []
    for res in results:
        if res["Raw_Report"] and res["Raw_Perception"]:
            db_payload.append(
                {
                    "task_id": batch_task_id,
                    "student_id": res["Student_ID"],
                    "question_id": resolved_question_id,
                    "total_deduction": float(res["Total_Deduction"]),
                    "is_pass": bool(res["Is_Fully_Correct"]),
                    "perception_output": res["Raw_Perception"],
                    "evaluation_report": res["Raw_Report"],
                }
            )

    if db_payload:
        inserted = await insert_grading_results(db_path, db_payload, task_id=batch_task_id)
        logger.info("Successfully persisted %s records to SQLite.", inserted)

    summary_file = output_dir / "summary.csv"
    headers = ["Student_ID", "Total_Deduction", "Is_Fully_Correct", "Requires_Human_Review", "Error_Status", "Status"]
    with open(summary_file, mode="w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    logger.info("--------------------------------------------------")
    logger.info("Batch processing complete.")
    logger.info("Database updated: %s", db_path)
    logger.info("Summary report generated: %s", summary_file)
    logger.info("--------------------------------------------------")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Batch processing interrupted by user.")
