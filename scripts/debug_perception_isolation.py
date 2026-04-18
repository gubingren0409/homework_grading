import argparse
import asyncio
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.exceptions import GradingSystemError
from src.perception.factory import create_perception_engine
from src.utils.file_parsers import UnsupportedFormatError, normalize_to_images
from src.utils.image_slicer import slice_image_by_layout


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def _safe_stem(path: Path) -> str:
    digest = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{path.stem}_{digest}"


def _guess_question_no(path: Path) -> Optional[str]:
    for part in path.parts:
        if part.startswith("question_"):
            suffix = part.split("question_", 1)[1]
            if suffix.isdigit():
                return str(int(suffix))
    return None


def _collect_files(input_root: Path, file_glob: str, max_files: int) -> List[Path]:
    files = sorted([p for p in input_root.rglob(file_glob) if p.is_file()])
    supported = {".jpg", ".jpeg", ".png", ".pdf"}
    filtered = [p for p in files if p.suffix.lower() in supported]
    if max_files > 0:
        return filtered[:max_files]
    return filtered


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _region_metrics(region: Any) -> Dict[str, Any]:
    width = float(region.bbox.x_max) - float(region.bbox.x_min)
    height = float(region.bbox.y_max) - float(region.bbox.y_min)
    return {
        "target_id": region.target_id,
        "question_no": region.question_no,
        "region_type": region.region_type,
        "width_norm": round(width, 6),
        "height_norm": round(height, 6),
        "is_suspicious_vertical_strip": bool(region.region_type == "answer_region" and width < 0.20 and height > 0.80),
    }


async def _analyze_one_file(
    *,
    source_path: Path,
    output_root: Path,
    context_type: str,
    explicit_target_question: Optional[str],
) -> Dict[str, Any]:
    engine = create_perception_engine()
    source_key = _safe_stem(source_path)
    source_out = output_root / source_key
    source_out.mkdir(parents=True, exist_ok=True)

    logger.info("Analyzing source: %s", source_path)
    raw_bytes = source_path.read_bytes()
    pages = await normalize_to_images(raw_bytes, source_path.name)

    target_question_no = explicit_target_question or _guess_question_no(source_path)
    source_summary: Dict[str, Any] = {
        "source_path": str(source_path),
        "source_key": source_key,
        "page_count": len(pages),
        "context_type": context_type,
        "target_question_no": target_question_no,
        "pages": [],
    }

    for page_idx, page_bytes in enumerate(pages):
        page_dir = source_out / f"page_{page_idx:03d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        page_summary: Dict[str, Any] = {
            "page_index": page_idx,
            "full_page_perception_ok": False,
            "layout_ok": False,
            "slice_count": 0,
            "slice_perception_ok_count": 0,
            "slice_perception_error_count": 0,
            "suspicious_regions": [],
        }

        full_page_result = await engine.process_image(page_bytes)
        full_page_payload = full_page_result.model_dump()
        _save_json(page_dir / "perception_full_page.json", full_page_payload)
        page_summary["full_page_perception_ok"] = True
        page_summary["full_page_element_count"] = len(full_page_result.elements)
        page_summary["full_page_readability_status"] = full_page_result.readability_status

        if not hasattr(engine, "extract_layout"):
            page_summary["layout_error"] = "engine does not support extract_layout"
            source_summary["pages"].append(page_summary)
            continue

        layout = await engine.extract_layout(  # type: ignore[attr-defined]
            page_bytes,
            context_type=context_type,
            target_question_no=target_question_no,
            page_index=page_idx,
        )
        _save_json(page_dir / "layout_ir.json", layout.model_dump())
        page_summary["layout_ok"] = True
        page_summary["slice_count"] = len(layout.regions)
        page_summary["question_no_null_count"] = sum(1 for r in layout.regions if r.question_no is None)

        region_metrics = [_region_metrics(r) for r in layout.regions]
        _save_json(page_dir / "layout_region_metrics.json", {"regions": region_metrics})
        page_summary["suspicious_regions"] = [r for r in region_metrics if r["is_suspicious_vertical_strip"]]

        slices = slice_image_by_layout(page_bytes, layout)
        slices_dir = page_dir / "slices"
        slices_dir.mkdir(parents=True, exist_ok=True)

        for region in layout.regions:
            region_id = str(region.target_id)
            slice_bytes = slices.get(region_id)
            if slice_bytes is None:
                page_summary["slice_perception_error_count"] += 1
                _save_json(
                    page_dir / f"slice_{region_id}_error.json",
                    {"error": "slice missing for layout region", "region_id": region_id},
                )
                continue

            (slices_dir / f"{region_id}.png").write_bytes(slice_bytes)
            try:
                slice_result = await engine.process_image(slice_bytes)
                _save_json(
                    page_dir / f"slice_{region_id}_perception.json",
                    slice_result.model_dump(),
                )
                page_summary["slice_perception_ok_count"] += 1
            except GradingSystemError as exc:
                page_summary["slice_perception_error_count"] += 1
                _save_json(
                    page_dir / f"slice_{region_id}_error.json",
                    {"error": str(exc), "region_id": region_id},
                )

        source_summary["pages"].append(page_summary)

    return source_summary


async def run(args: argparse.Namespace) -> None:
    input_root = Path(args.input_root)
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    files = _collect_files(input_root, args.file_glob, args.max_files)
    if not files:
        raise FileNotFoundError(f"No supported files found under {input_root} with pattern {args.file_glob}")

    logger.info("Found %s files for isolated perception debug", len(files))
    run_summary: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "input_root": str(input_root),
        "file_glob": args.file_glob,
        "max_files": args.max_files,
        "context_type": args.context_type,
        "target_question_no": args.target_question_no,
        "files": [],
    }

    for source_path in files:
        try:
            file_summary = await _analyze_one_file(
                source_path=source_path,
                output_root=output_root,
                context_type=args.context_type,
                explicit_target_question=args.target_question_no,
            )
            run_summary["files"].append(file_summary)
        except (UnsupportedFormatError, GradingSystemError, ValueError) as exc:
            run_summary["files"].append(
                {
                    "source_path": str(source_path),
                    "error": str(exc),
                }
            )

    _save_json(output_root / "run_summary.json", run_summary)
    logger.info("Perception isolation debug artifacts written to: %s", output_root)


def parse_args() -> argparse.Namespace:
    default_out = Path("outputs") / f"perception_isolation_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    parser = argparse.ArgumentParser(description="Isolated Perception/Phase35 debug runner using real data samples.")
    parser.add_argument(
        "--input_root",
        type=str,
        default="data\\3.20_physics",
        help="Root folder containing real photos/PDFs.",
    )
    parser.add_argument(
        "--file_glob",
        type=str,
        default="**\\students\\stu_ans_01.*",
        help="Glob pattern relative to input_root (recursive via rglob).",
    )
    parser.add_argument(
        "--max_files",
        type=int,
        default=3,
        help="Maximum number of source files to process (<=0 means all).",
    )
    parser.add_argument(
        "--context_type",
        type=str,
        default="STUDENT_ANSWER",
        choices=["REFERENCE", "STUDENT_ANSWER"],
    )
    parser.add_argument(
        "--target_question_no",
        type=str,
        default=None,
        help="Optional explicit target question number for layout extraction.",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(default_out),
        help="Directory for JSON artifacts and slices.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
