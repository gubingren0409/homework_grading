"""
Q18 调试 - 阶段 B：仅认知层（DeepSeek）跑测

读取阶段 A 缓存的感知 IR + 既有 reference_rubric.json，
对每份学生作答只调一次 evaluate_logic，输出：
    outputs/q18_debug/runs/<run_name>/
        ├─ summary.md            人工对照报告（含图片相对链接）
        ├─ summary.json          机读汇总
        └─ reports/<stem>.json   每份完整 EvaluationReport

prompt 改完直接重跑这个脚本即可，不会触发感知层调用。

用法（在 homework_grader_system/ 目录下执行）：
    python scripts/debug_q18_eval_only.py --run-name v1.0.2-baseline
    python scripts/debug_q18_eval_only.py --run-name v1.0.3 --concurrency 2 --only stu_ans_03,stu_ans_07
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine  # noqa: E402
from src.schemas.cognitive_ir import EvaluationReport  # noqa: E402
from src.schemas.perception_ir import PerceptionOutput  # noqa: E402
from src.schemas.rubric_ir import TeacherRubric  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("q18_eval")

DEFAULT_DEBUG_ROOT = REPO_ROOT / "outputs" / "q18_debug"
DEFAULT_RUBRIC = REPO_ROOT / "data" / "3.20_physics" / "question_18" / "reference_rubric.json"
DEFAULT_PROMPT_FILE = REPO_ROOT / "configs" / "prompts" / "deepseek.cognitive.evaluate.json"


def _extract_retry_seconds(message: str) -> float | None:
    match = re.search(r"Retry in ([0-9]+(?:\.[0-9]+)?)s", message)
    if not match:
        return None
    return float(match.group(1))


def _is_transient_eval_error(message: str) -> bool:
    transient_markers = [
        "Probe in progress",
        "Circuit breaker active",
        "Persistent network instability",
        "Request timed out",
        "Retry in ",
    ]
    return any(marker in message for marker in transient_markers)


async def _evaluate_one(
    engine: DeepSeekCognitiveEngine,
    ir_path: Path,
    rubric: TeacherRubric,
    reports_dir: Path,
    *,
    max_attempts: int,
    retry_delay_seconds: float,
) -> dict:
    stem = ir_path.stem
    ir = PerceptionOutput.model_validate_json(ir_path.read_text(encoding="utf-8"))
    logger.info("evaluating %s (%d elements)", stem, len(ir.elements))
    started = datetime.now()
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            report: EvaluationReport = await engine.evaluate_logic(ir, rubric)
            elapsed = (datetime.now() - started).total_seconds()
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            message = str(exc)
            if attempt < max_attempts and _is_transient_eval_error(message):
                wait_seconds = _extract_retry_seconds(message) or retry_delay_seconds
                wait_seconds = max(wait_seconds + 2.0, retry_delay_seconds)
                logger.warning(
                    "Transient eval failure for %s on attempt %s/%s: %s. Retrying in %.1fs.",
                    stem,
                    attempt,
                    max_attempts,
                    message,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue
            logger.exception("FAILED %s", stem)
            return {"stem": stem, "status": "ERROR", "error": repr(exc)}

    assert last_error is None or "report" in locals()

    out_path = reports_dir / f"{stem}.json"
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    return {
        "stem": stem,
        "status": report.status,
        "is_fully_correct": report.is_fully_correct,
        "total_score_deduction": report.total_score_deduction,
        "system_confidence": report.system_confidence,
        "requires_human_review": report.requires_human_review,
        "step_count": len(report.step_evaluations),
        "overall_feedback": report.overall_feedback,
        "step_evaluations": [s.model_dump() for s in report.step_evaluations],
        "elapsed_seconds": round(elapsed, 2),
        "report_path": str(out_path),
        "ir_readability": ir.readability_status,
        "ir_global_confidence": ir.global_confidence,
        "ir_element_count": len(ir.elements),
        "ir_is_blank": ir.is_blank,
    }


def _render_markdown(
    run_dir: Path,
    debug_root: Path,
    rubric: TeacherRubric,
    prompt_meta: dict,
    items: list[dict],
    rubric_total: float,
) -> None:
    md = []
    question_id = (rubric.question_id or "").strip()
    if question_id.lower().startswith("q"):
        rubric_label = f"Q{question_id[1:]}"
    else:
        rubric_label = f"Q{question_id}" if question_id else "题目"
    md.append(f"# {rubric_label} 认知层批改对照报告 — `{run_dir.name}`\n")
    md.append(f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    md.append(f"- Prompt key: `{prompt_meta.get('prompt_key')}`  version: `{prompt_meta.get('version')}`  hash: `{prompt_meta.get('version_hash')}`")
    md.append(f"- Rubric: {len(rubric.grading_points)} 个评分点，总分 {rubric_total} 分")
    ok = [it for it in items if it.get("status") == "SCORED"]
    rejected = [it for it in items if it.get("status") == "REJECTED_UNREADABLE"]
    errors = [it for it in items if it.get("status") == "ERROR"]
    md.append(f"- 样本: 共 {len(items)}，SCORED {len(ok)}，REJECTED {len(rejected)}，ERROR {len(errors)}\n")

    md.append("## 汇总表\n")
    md.append("| # | 样本 | 状态 | 扣分 | 得分 | 全对 | 复核 | 置信 | 步骤数 | 耗时(s) |")
    md.append("|---|---|---|---:|---:|:-:|:-:|---:|---:|---:|")
    for i, it in enumerate(items, 1):
        if it.get("status") == "ERROR":
            md.append(f"| {i} | {it['stem']} | ERROR | - | - | - | - | - | - | - |")
            continue
        deduct = it["total_score_deduction"]
        score = max(0.0, rubric_total - deduct)
        md.append(
            f"| {i} | [{it['stem']}](../../images/{it['stem']}.png) | {it['status']} | "
            f"{deduct:g} | {score:g} | {'✅' if it['is_fully_correct'] else '❌'} | "
            f"{'⚠️' if it['requires_human_review'] else ' '} | "
            f"{it['system_confidence']:.2f} | {it['step_count']} | {it['elapsed_seconds']:.1f} |"
        )
    md.append("")

    md.append("## 逐份详情\n")
    for it in items:
        md.append(f"### {it['stem']}")
        md.append(f"![{it['stem']}](../../images/{it['stem']}.png)\n")
        if it.get("status") == "ERROR":
            md.append(f"- ❗ ERROR: `{it.get('error')}`\n")
            continue
        deduct = it["total_score_deduction"]
        score = max(0.0, rubric_total - deduct)
        md.append(f"- 状态: **{it['status']}** | 扣分 **{deduct:g}** / 得分 **{score:g}** / 总分 {rubric_total:g}")
        md.append(f"- is_fully_correct: {it['is_fully_correct']} | requires_human_review: {it['requires_human_review']} | confidence: {it['system_confidence']:.2f}")
        md.append(f"- 感知层: readability={it['ir_readability']}  global_conf={it['ir_global_confidence']:.2f}  elements={it['ir_element_count']}  is_blank={it['ir_is_blank']}")
        md.append(f"- 完整 report: [`reports/{it['stem']}.json`](reports/{it['stem']}.json)")
        md.append("")
        md.append("**综合反馈**：")
        md.append(f"> {it['overall_feedback']}\n")
        if it["step_evaluations"]:
            md.append("**逐步评分**：\n")
            md.append("| element_id | 正确 | 错误类型 | 修正建议 |")
            md.append("|---|:-:|---|---|")
            for st in it["step_evaluations"]:
                tick = "✅" if st["is_correct"] else "❌"
                err = st.get("error_type") or ""
                sug = (st.get("correction_suggestion") or "").replace("\n", " ").replace("|", "\\|")
                md.append(f"| `{st['reference_element_id']}` | {tick} | {err} | {sug} |")
            md.append("")

    (run_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    debug_root = Path(args.debug_root)
    cache_dir = debug_root / "perception_cache"
    if not cache_dir.exists():
        raise FileNotFoundError(f"Perception cache not found: {cache_dir}\n请先运行 scripts/debug_q18_cache_perception.py")

    rubric_path = Path(args.rubric_file)
    rubric = TeacherRubric.model_validate_json(rubric_path.read_text(encoding="utf-8"))
    rubric_total = sum(p.score for p in rubric.grading_points)

    prompt_meta = json.loads(Path(args.prompt_file).read_text(encoding="utf-8")).get("meta", {})

    only_filter: set[str] | None = None
    if args.only:
        only_filter = {x.strip() for x in args.only.split(",") if x.strip()}

    ir_files = sorted(p for p in cache_dir.glob("*.json"))
    if only_filter:
        ir_files = [p for p in ir_files if p.stem in only_filter]
    if not ir_files:
        raise FileNotFoundError(f"No IR cache files matched in {cache_dir}")

    run_name = args.run_name or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = debug_root / "runs" / run_name
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Run: %s | samples=%d | prompt=%s@%s",
                run_name, len(ir_files), prompt_meta.get("prompt_key"), prompt_meta.get("version"))

    engine = DeepSeekCognitiveEngine()
    sem = asyncio.Semaphore(args.concurrency)
    items: list[dict] = []

    async def _bounded(p: Path) -> None:
        async with sem:
            items.append(
                await _evaluate_one(
                    engine,
                    p,
                    rubric,
                    reports_dir,
                    max_attempts=args.retry_attempts,
                    retry_delay_seconds=args.retry_delay_seconds,
                )
            )

    started = datetime.now()
    await asyncio.gather(*[_bounded(p) for p in ir_files])
    elapsed = (datetime.now() - started).total_seconds()
    items.sort(key=lambda it: it["stem"])

    summary = {
        "run_name": run_name,
        "timestamp": datetime.now().isoformat(),
        "prompt_meta": prompt_meta,
        "rubric_question_id": rubric.question_id,
        "rubric_total_score": rubric_total,
        "rubric_points": len(rubric.grading_points),
        "samples_total": len(items),
        "samples_scored": sum(1 for it in items if it.get("status") == "SCORED"),
        "samples_rejected": sum(1 for it in items if it.get("status") == "REJECTED_UNREADABLE"),
        "samples_error": sum(1 for it in items if it.get("status") == "ERROR"),
        "elapsed_seconds": round(elapsed, 2),
        "items": items,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _render_markdown(run_dir, debug_root, rubric, prompt_meta, items, rubric_total)

    logger.info("DONE  scored=%d rejected=%d error=%d elapsed=%.1fs",
                summary["samples_scored"], summary["samples_rejected"], summary["samples_error"], elapsed)
    logger.info("Markdown: %s", run_dir / "summary.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cognitive-only iteration runner for a target question.")
    parser.add_argument("--debug_root", type=str, default=str(DEFAULT_DEBUG_ROOT))
    parser.add_argument("--rubric_file", type=str, default=str(DEFAULT_RUBRIC))
    parser.add_argument("--prompt_file", type=str, default=str(DEFAULT_PROMPT_FILE),
                        help="仅用于把 prompt meta 写进报告做溯源")
    parser.add_argument("--run-name", dest="run_name", type=str, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--retry-attempts",
        dest="retry_attempts",
        type=int,
        default=3,
        help="Max attempts per sample for transient DeepSeek/circuit-breaker failures.",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        dest="retry_delay_seconds",
        type=float,
        default=10.0,
        help="Base retry delay for transient evaluation failures.",
    )
    parser.add_argument("--only", type=str, default=None,
                        help="仅跑指定样本，逗号分隔，例如 stu_ans_03,stu_ans_07")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
