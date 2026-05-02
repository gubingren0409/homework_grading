"""
Q18 调试 - 阶段 A：感知层一次性缓存

为 question_18/students/ 下的每份学生作答跑一次感知层（Qwen-VL），
把合并后的 PerceptionOutput 序列化到 outputs/q18_debug/perception_cache/，
并把原图复制到 outputs/q18_debug/images/ 方便后续 markdown 报告引用。

之后认知层的 prompt 迭代调试就只读这些缓存文件，不再发感知请求，
显著降低成本与时延。

用法（在 homework_grader_system/ 目录下执行）：
    python scripts/debug_q18_cache_perception.py
    python scripts/debug_q18_cache_perception.py --concurrency 3 --force
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 允许直接以脚本方式运行
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.perception.factory import create_perception_engine  # noqa: E402
from src.schemas.perception_ir import PerceptionOutput  # noqa: E402
from src.utils.file_parsers import process_multiple_files  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("q18_cache")

DEFAULT_STUDENTS_DIR = REPO_ROOT / "data" / "3.20_physics" / "question_18" / "students"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "q18_debug"


async def _perceive_one(
    engine,
    image_path: Path,
    cache_dir: Path,
    images_dir: Path,
    force: bool,
) -> dict:
    out_json = cache_dir / f"{image_path.stem}.json"
    img_copy = images_dir / image_path.name

    if out_json.exists() and not force:
        logger.info("skip (cached): %s", image_path.name)
        return {"file": image_path.name, "status": "cached", "path": str(out_json)}

    raw = image_path.read_bytes()
    pages = await process_multiple_files([(raw, image_path.name)])
    logger.info("perceiving %s (%d page(s))", image_path.name, len(pages))

    page_outputs = await asyncio.gather(
        *[engine.process_image(pb) for pb in pages]
    )

    # 与 workflow._evaluate_from_images 保持一致：合并多页元素并重写 element_id
    all_elements = []
    all_blank = True
    worst_status = "CLEAR"
    statuses_priority = {"CLEAR": 0, "MINOR_ALTERATION": 1, "HEAVILY_ALTERED": 2, "UNREADABLE": 3}
    for page_idx, po in enumerate(page_outputs):
        if not po.is_blank:
            all_blank = False
        if statuses_priority[po.readability_status] > statuses_priority[worst_status]:
            worst_status = po.readability_status
        for elem in po.elements:
            elem.element_id = f"p{page_idx}_{elem.element_id}"
            all_elements.append(elem)

    avg_conf = (
        sum(p.global_confidence for p in page_outputs) / len(page_outputs)
        if page_outputs else 0.0
    )

    merged = PerceptionOutput(
        readability_status=worst_status,  # 保留真实状态供认知层降级判定
        elements=all_elements,
        global_confidence=avg_conf,
        is_blank=all_blank,
        trigger_short_circuit=any(p.trigger_short_circuit for p in page_outputs),
    )

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(merged.model_dump_json(indent=2), encoding="utf-8")

    images_dir.mkdir(parents=True, exist_ok=True)
    if not img_copy.exists():
        shutil.copy2(image_path, img_copy)

    return {
        "file": image_path.name,
        "status": "ok",
        "elements": len(all_elements),
        "readability": worst_status,
        "is_blank": all_blank,
        "global_confidence": round(avg_conf, 3),
        "path": str(out_json),
    }


async def run(students_dir: Path, output_dir: Path, concurrency: int, force: bool) -> None:
    cache_dir = output_dir / "perception_cache"
    images_dir = output_dir / "images"
    cache_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(
        p for p in students_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf"}
    )
    if not files:
        raise FileNotFoundError(f"No student files under {students_dir}")
    logger.info("Found %d student files in %s", len(files), students_dir)

    engine = create_perception_engine()
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []
    failures: list[dict] = []

    async def _bounded(p: Path) -> None:
        async with sem:
            try:
                results.append(await _perceive_one(engine, p, cache_dir, images_dir, force))
            except Exception as exc:  # noqa: BLE001
                logger.exception("FAILED %s", p.name)
                failures.append({"file": p.name, "error": repr(exc)})

    started = datetime.now()
    await asyncio.gather(*[_bounded(p) for p in files])
    elapsed = (datetime.now() - started).total_seconds()

    summary = {
        "students_dir": str(students_dir),
        "cache_dir": str(cache_dir),
        "images_dir": str(images_dir),
        "total": len(files),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "cached": sum(1 for r in results if r["status"] == "cached"),
        "failed": len(failures),
        "elapsed_seconds": round(elapsed, 2),
        "results": results,
        "failures": failures,
    }
    (output_dir / "perception_cache_summary.json").write_text(
        __import__("json").dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "DONE  ok=%d cached=%d failed=%d elapsed=%.1fs",
        summary["ok"], summary["cached"], summary["failed"], elapsed,
    )
    if failures:
        logger.warning("Failures: %s", failures)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cache Q18 perception IRs for cognitive prompt iteration.")
    parser.add_argument("--students_dir", type=str, default=str(DEFAULT_STUDENTS_DIR))
    parser.add_argument("--output_dir", type=str, default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="重新感知已缓存的样本")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(
        students_dir=Path(args.students_dir),
        output_dir=Path(args.output_dir),
        concurrency=args.concurrency,
        force=args.force,
    ))


if __name__ == "__main__":
    main()
