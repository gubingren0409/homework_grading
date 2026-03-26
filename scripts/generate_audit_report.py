import argparse
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def _normalize_student_id(student_id: Optional[str]) -> str:
    """
    规范化 student_id：
    - 'stu_ans_01.png' -> 'stu_ans_01'
    - None -> ''
    """
    if not student_id:
        return ""
    return Path(student_id).stem


@dataclass(frozen=True)
class QuestionAssets:
    """
    单题样本素材索引：
    - standard_images: 标准答案图片列表
    - student_images: 学生作答图片映射（key=样本名 stem，如 stu_ans_01）
    """

    question_key: str
    standard_images: List[Path]
    student_images: Dict[str, Path]


@dataclass(frozen=True)
class DbResultRow:
    """
    SQLite 批改结果记录的结构化表示。
    """

    id: int
    task_id: Optional[str]
    student_id: Optional[str]
    question_id: Optional[str]
    total_deduction: float
    is_pass: bool
    report_json: str
    created_at: Optional[str]


def _to_markdown_path(path: Path, md_parent: Path) -> str:
    """
    将绝对路径转换为 Markdown 友好的相对路径（统一使用 / 分隔符）。
    """
    relative = Path(os.path.relpath(path.resolve(), md_parent.resolve()))
    return str(relative).replace("\\", "/")


def _safe_json_loads(text: str) -> Dict[str, Any]:
    """
    安全解析 JSON 字符串；若失败返回带错误信息的字典，避免脚本崩溃。
    """
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"_raw_non_dict": parsed}
    except json.JSONDecodeError as exc:
        return {"_json_parse_error": str(exc), "_raw_text": text}


def _extract_payload_sections(report_payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    兼容多种 report_json 结构：
    1) {"perception_output": {...}, "evaluation_report": {...}}
    2) 仅 EvaluationReport 平铺结构
    """
    perception_output = report_payload.get("perception_output")
    evaluation_report = report_payload.get("evaluation_report")

    # 兼容旧格式：report_json 直接就是 EvaluationReport
    if evaluation_report is None and {"is_fully_correct", "total_score_deduction", "step_evaluations"}.issubset(
        set(report_payload.keys())
    ):
        evaluation_report = report_payload

    if perception_output is not None and not isinstance(perception_output, dict):
        perception_output = {"_invalid_perception_payload": perception_output}
    if evaluation_report is not None and not isinstance(evaluation_report, dict):
        evaluation_report = {"_invalid_evaluation_payload": evaluation_report}

    return perception_output, evaluation_report


def _find_reasoning_trace(evaluation_report: Dict[str, Any]) -> str:
    """
    尝试从 DeepSeek 输出中提取可见的“逻辑推导/思维链”字段。
    注意：若模型未显式返回思维链，本函数会回退到结构化步骤摘要，避免凭空臆造。
    """
    candidate_keys = [
        "reasoning_trace",
        "chain_of_thought",
        "thought_process",
        "analysis",
        "rationale",
        "reasoning",
    ]
    for key in candidate_keys:
        value = evaluation_report.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    step_evaluations = evaluation_report.get("step_evaluations")
    if isinstance(step_evaluations, list) and step_evaluations:
        lines: List[str] = []
        for idx, step in enumerate(step_evaluations, start=1):
            if not isinstance(step, dict):
                continue
            ref = step.get("reference_element_id", f"step_{idx}")
            is_correct = bool(step.get("is_correct"))
            error_type = step.get("error_type") or "NONE"
            suggestion = step.get("correction_suggestion") or "无补充说明。"
            state = "正确" if is_correct else "错误"
            lines.append(f"[{idx}] 锚点 `{ref}` -> {state}（{error_type}）：{suggestion}")
        if lines:
            return "模型未显式输出思维链字段，以下为基于 `step_evaluations` 的可审查推导摘要：\n" + "\n".join(lines)

    return "模型结果中未提供可解析的逻辑推导文本。"


def _render_perception_section(perception_output: Optional[Dict[str, Any]]) -> str:
    """
    渲染 Qwen 感知层内容，重点高亮 image_diagram 的文字转译。
    """
    if perception_output is None:
        return "## 2) Qwen 感知层输出\n\n> 当前记录不包含感知层落盘数据（可能由旧版本脚本写入）。\n"

    readability = perception_output.get("readability_status", "UNKNOWN")
    confidence = perception_output.get("global_confidence", "UNKNOWN")
    elements = perception_output.get("elements", [])
    if not isinstance(elements, list):
        elements = []

    lines: List[str] = [
        "## 2) Qwen 感知层输出",
        "",
        f"- `readability_status`: **{readability}**",
        f"- `global_confidence`: **{confidence}**",
        "",
        "### 2.1 结构化元素明细",
        "",
        "| element_id | content_type | confidence | raw_content |",
        "|---|---|---:|---|",
    ]

    image_diagram_blocks: List[str] = []
    for elem in elements:
        if not isinstance(elem, dict):
            continue
        element_id = str(elem.get("element_id", "N/A"))
        content_type = str(elem.get("content_type", "N/A"))
        element_conf = elem.get("confidence_score", "N/A")
        raw_content = str(elem.get("raw_content", "")).replace("\n", " ")
        lines.append(f"| `{element_id}` | `{content_type}` | {element_conf} | {raw_content} |")

        if content_type == "image_diagram":
            image_diagram_blocks.append(
                "\n".join(
                    [
                        f"#### image_diagram 高亮：`{element_id}`",
                        "",
                        "```text",
                        str(elem.get("raw_content", "")).strip(),
                        "```",
                    ]
                )
            )

    lines.append("")
    lines.append("### 2.2 image_diagram 转译高亮")
    lines.append("")
    if image_diagram_blocks:
        lines.extend(image_diagram_blocks)
    else:
        lines.append("> 本样本无 `image_diagram` 节点。")

    return "\n".join(lines) + "\n"


def _render_evaluation_section(evaluation_report: Optional[Dict[str, Any]]) -> str:
    """
    渲染 DeepSeek 认知层输出，包括最终判断、错误锚点与逻辑推导展示。
    """
    if evaluation_report is None:
        return "## 3) DeepSeek 认知层输出\n\n> 当前记录缺少认知层报告，无法展示推导与判定。\n"

    is_fully_correct = evaluation_report.get("is_fully_correct", "UNKNOWN")
    total_deduction = evaluation_report.get("total_score_deduction", "UNKNOWN")
    requires_human_review = evaluation_report.get("requires_human_review", "UNKNOWN")
    system_confidence = evaluation_report.get("system_confidence", "UNKNOWN")
    overall_feedback = evaluation_report.get("overall_feedback", "")
    reasoning_trace = _find_reasoning_trace(evaluation_report)

    step_evaluations = evaluation_report.get("step_evaluations", [])
    if not isinstance(step_evaluations, list):
        step_evaluations = []

    wrong_anchors: List[str] = []
    rows: List[str] = [
        "| 锚点(reference_element_id) | 正误 | error_type | correction_suggestion |",
        "|---|---|---|---|",
    ]
    for idx, step in enumerate(step_evaluations, start=1):
        if not isinstance(step, dict):
            continue
        anchor = str(step.get("reference_element_id", f"step_{idx}"))
        is_correct = bool(step.get("is_correct"))
        status = "正确" if is_correct else "错误"
        error_type = str(step.get("error_type", "NONE"))
        suggestion = str(step.get("correction_suggestion", "")).replace("\n", " ")
        rows.append(f"| `{anchor}` | {status} | `{error_type}` | {suggestion} |")
        if not is_correct:
            wrong_anchors.append(anchor)

    return "\n".join(
        [
            "## 3) DeepSeek 认知层输出",
            "",
            f"- 最终判定 `is_fully_correct`: **{is_fully_correct}**",
            f"- 扣分 `total_score_deduction`: **{total_deduction}**",
            f"- 人工复核标记 `requires_human_review`: **{requires_human_review}**",
            f"- 系统置信度 `system_confidence`: **{system_confidence}**",
            "",
            "### 3.1 逻辑推导（可审查视图）",
            "",
            "```text",
            reasoning_trace,
            "```",
            "",
            "### 3.2 最终反馈",
            "",
            f"> {overall_feedback}",
            "",
            "### 3.3 错误步骤锚点",
            "",
            f"- 错误锚点数量：**{len(wrong_anchors)}**",
            f"- 错误锚点列表：{', '.join(f'`{a}`' for a in wrong_anchors) if wrong_anchors else '无'}",
            "",
            "### 3.4 Step 级别明细",
            "",
            *rows,
            "",
        ]
    )


def _scan_question_assets(data_root: Path) -> Dict[str, QuestionAssets]:
    """
    扫描 data/3.20_physics 数据集，构建题目素材索引。
    """
    question_assets: Dict[str, QuestionAssets] = {}
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    for question_dir in sorted([p for p in data_root.iterdir() if p.is_dir() and p.name.startswith("question_")]):
        standard_dir = question_dir / "standard"
        students_dir = question_dir / "students"

        standard_images = sorted(
            [p for p in standard_dir.iterdir() if p.is_file() and p.suffix.lower() in image_exts]
        ) if standard_dir.exists() else []

        student_images: Dict[str, Path] = {}
        if students_dir.exists():
            for student_img in sorted([p for p in students_dir.iterdir() if p.is_file() and p.suffix.lower() in image_exts]):
                student_images[student_img.stem] = student_img

        question_assets[question_dir.name] = QuestionAssets(
            question_key=question_dir.name,
            standard_images=standard_images,
            student_images=student_images,
        )

    return question_assets


def _load_db_rows(db_path: Path, question_filter: Optional[str] = None, task_filter: Optional[str] = None) -> List[DbResultRow]:
    """
    从 SQLite 读取批改结果，按 id 倒序，方便后续做“最新记录优先”去重。
    """
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite 文件不存在: {db_path}")

    query = """
        SELECT id, task_id, student_id, question_id, total_deduction, is_pass, report_json, created_at
        FROM grading_results
    """
    clauses: List[str] = []
    params: List[Any] = []
    if question_filter:
        clauses.append("question_id = ?")
        params.append(question_filter)
    if task_filter:
        clauses.append("task_id = ?")
        params.append(task_filter)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"

    rows: List[DbResultRow] = []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(query, params)
        for row in cursor.fetchall():
            rows.append(
                DbResultRow(
                    id=int(row["id"]),
                    task_id=row["task_id"],
                    student_id=row["student_id"],
                    question_id=row["question_id"],
                    total_deduction=float(row["total_deduction"]) if row["total_deduction"] is not None else 0.0,
                    is_pass=bool(row["is_pass"]),
                    report_json=row["report_json"] or "{}",
                    created_at=row["created_at"],
                )
            )
    finally:
        conn.close()
    return rows


def _resolve_question_key(
    row: DbResultRow,
    question_assets: Mapping[str, QuestionAssets],
    student_to_questions: Mapping[str, List[str]],
) -> Optional[str]:
    """
    将 DB 记录尽量映射到 question_xx 目录：
    1) question_id 直接命中
    2) question_id / task_id 中包含 question_xx 模式
    3) student_id 在数据集中唯一归属
    """
    if row.question_id and row.question_id in question_assets:
        return row.question_id

    import re

    pattern = re.compile(r"(question_\d+)")
    for candidate in [row.question_id or "", row.task_id or ""]:
        match = pattern.search(candidate)
        if match and match.group(1) in question_assets:
            return match.group(1)

    if row.student_id:
        question_candidates = student_to_questions.get(_normalize_student_id(row.student_id), [])
        if len(question_candidates) == 1:
            return question_candidates[0]

    return None


def _build_student_to_questions_map(question_assets: Mapping[str, QuestionAssets]) -> Dict[str, List[str]]:
    """
    生成 student_id -> [question_key] 反向索引，用于解决 DB 映射歧义。
    """
    mapping: Dict[str, List[str]] = {}
    for question_key, assets in question_assets.items():
        for student_id in assets.student_images:
            mapping.setdefault(student_id, []).append(question_key)
    return mapping


def _deduplicate_latest(rows: Sequence[DbResultRow], question_keys: Mapping[int, Optional[str]]) -> List[DbResultRow]:
    """
    对 (question_key, student_id) 去重，仅保留最新一条（输入已按 id DESC）。
    """
    latest: List[DbResultRow] = []
    seen: set[Tuple[Optional[str], Optional[str]]] = set()

    for row in rows:
        key = (question_keys.get(row.id), _normalize_student_id(row.student_id))
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)

    return latest


def _render_report_markdown(
    row: DbResultRow,
    md_path: Path,
    question_key: Optional[str],
    assets: Optional[QuestionAssets],
) -> str:
    """
    生成单个样本的 Markdown 审查报告内容。
    """
    payload = _safe_json_loads(row.report_json)
    perception_output, evaluation_report = _extract_payload_sections(payload)

    lines: List[str] = [
        f"# 审查报告：{row.student_id or 'unknown_student'}",
        "",
        "## 1) 样本与任务元信息",
        "",
        f"- `db_id`: `{row.id}`",
        f"- `task_id`: `{row.task_id}`",
        f"- `question_id(DB)`: `{row.question_id}`",
        f"- `question_key(映射)`: `{question_key}`",
        f"- `created_at`: `{row.created_at}`",
        f"- `is_pass`: **{row.is_pass}**",
        f"- `total_deduction`: **{row.total_deduction}**",
        "",
    ]

    lines.append("## 1.1 标准答案与学生作答图片")
    lines.append("")

    if assets:
        if assets.standard_images:
            lines.append("### 标准答案")
            lines.append("")
            for standard_image in assets.standard_images:
                lines.append(f"![standard-{standard_image.stem}]({_to_markdown_path(standard_image, md_path.parent)})")
            lines.append("")
        else:
            lines.append("> 未找到标准答案图片。")
            lines.append("")

        normalized_student_id = _normalize_student_id(row.student_id)
        if normalized_student_id and normalized_student_id in assets.student_images:
            student_image = assets.student_images[normalized_student_id]
            lines.append("### 学生作答")
            lines.append("")
            lines.append(f"![student-{normalized_student_id}]({_to_markdown_path(student_image, md_path.parent)})")
            lines.append("")
        else:
            lines.append("> 未找到与该记录匹配的学生原图。")
            lines.append("")
    else:
        lines.append("> 未能定位 question 目录，无法自动关联图片。")
        lines.append("")

    lines.append(_render_perception_section(perception_output))
    lines.append(_render_evaluation_section(evaluation_report))

    lines.append("## 4) 原始 JSON（审计留痕）")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def generate_audit_reports(
    data_root: Path,
    db_path: Path,
    output_dir: Path,
    question_filter: Optional[str] = None,
    task_filter: Optional[str] = None,
) -> int:
    """
    生成离线可视化审查报告主流程。
    """
    question_assets = _scan_question_assets(data_root)
    student_to_questions = _build_student_to_questions_map(question_assets)
    rows = _load_db_rows(db_path, question_filter=question_filter, task_filter=task_filter)
    if not rows:
        logger.warning("未查询到任何批改结果记录。")
        return 0

    question_key_map: Dict[int, Optional[str]] = {
        row.id: _resolve_question_key(row, question_assets, student_to_questions) for row in rows
    }
    latest_rows = _deduplicate_latest(rows, question_key_map)

    generated_count = 0
    for row in latest_rows:
        question_key = question_key_map.get(row.id)
        assets = question_assets.get(question_key) if question_key else None

        if question_key:
            md_dir = output_dir / question_key
            md_name = f"{row.student_id or f'db_{row.id}'}.md"
        else:
            md_dir = output_dir / "_unresolved"
            md_name = f"{row.student_id or 'unknown'}__db_{row.id}.md"

        md_dir.mkdir(parents=True, exist_ok=True)
        md_path = md_dir / md_name

        content = _render_report_markdown(row, md_path, question_key, assets)
        md_path.write_text(content, encoding="utf-8")
        generated_count += 1

    logger.info("审查报告生成完成，共输出 %s 份：%s", generated_count, output_dir)
    return generated_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate offline markdown audit reports from SQLite grading results.")
    parser.add_argument(
        "--data_root",
        type=str,
        default="data/3.20_physics",
        help="数据集根目录（包含 question_xx 子目录）。",
    )
    parser.add_argument(
        "--db_path",
        type=str,
        default="outputs/grading_database.db",
        help="SQLite 数据库路径。",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="outputs/audit_reports",
        help="审查报告输出目录。",
    )
    parser.add_argument(
        "--question_id",
        type=str,
        default=None,
        help="可选：只生成指定 question_id 的记录（按 DB 字段过滤）。",
    )
    parser.add_argument(
        "--task_id",
        type=str,
        default=None,
        help="可选：只生成指定 task_id 的记录。",
    )
    args = parser.parse_args()

    count = generate_audit_reports(
        data_root=Path(args.data_root),
        db_path=Path(args.db_path),
        output_dir=Path(args.output_dir),
        question_filter=args.question_id,
        task_filter=args.task_id,
    )
    logger.info("Done. generated=%s", count)


if __name__ == "__main__":
    main()
