from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import settings
from teacher_batch_runner import OUTPUTS_DIR, REFERENCE_DIR, STUDENTS_DIR, TRIAL_DIR


def _generated_crop_dir() -> Path:
    return settings.uploads_path / "paper_crops"


def _safe_reset_directory(path: Path) -> None:
    path = path.resolve()
    allowed = {
        OUTPUTS_DIR.resolve(),
        REFERENCE_DIR.resolve(),
        STUDENTS_DIR.resolve(),
        _generated_crop_dir().resolve(),
    }
    if path not in allowed:
        raise ValueError(f"refusing to reset unmanaged path: {path}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _count_items(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.rglob("*"))


def build_teacher_trial_data_summary() -> dict[str, dict[str, object]]:
    crop_dir = _generated_crop_dir()
    return {
        "reference_dir": {"path": str(REFERENCE_DIR), "items": _count_items(REFERENCE_DIR)},
        "students_dir": {"path": str(STUDENTS_DIR), "items": _count_items(STUDENTS_DIR)},
        "outputs_dir": {"path": str(OUTPUTS_DIR), "items": _count_items(OUTPUTS_DIR)},
        "generated_crop_dir": {"path": str(crop_dir), "items": _count_items(crop_dir)},
    }


def reset_teacher_trial_data(*, remove_inputs: bool = False) -> list[Path]:
    targets = [OUTPUTS_DIR, _generated_crop_dir()]
    if remove_inputs:
        targets.extend([REFERENCE_DIR, STUDENTS_DIR])
    for target in targets:
        _safe_reset_directory(target)
    return [target.resolve() for target in targets]


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or clean local teacher trial data.")
    parser.add_argument(
        "--cleanup-generated",
        action="store_true",
        help="Delete generated outputs and crop artifacts, then recreate empty directories.",
    )
    parser.add_argument(
        "--cleanup-inputs",
        action="store_true",
        help="Also delete teacher_trial/reference and teacher_trial/students.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required together with --cleanup-inputs to confirm input deletion.",
    )
    args = parser.parse_args()

    if args.cleanup_inputs and not args.yes:
        print("如需删除 reference/students 中的原始输入，请同时传入 --yes。")
        return 1

    if args.cleanup_generated or args.cleanup_inputs:
        removed = reset_teacher_trial_data(remove_inputs=args.cleanup_inputs)
        print("本地教师版数据目录已重置：")
        for path in removed:
            print(f"- {path}")
        return 0

    summary = build_teacher_trial_data_summary()
    print("=== 本地教师版数据目录 ===")
    print(f"试用根目录：{TRIAL_DIR}")
    for key, payload in summary.items():
        print(f"- {key}: {payload['path']}（items={payload['items']}）")
    print("")
    print("默认报告位置：teacher_trial\\outputs")
    print("如需清理生成物：python scripts\\manage_teacher_trial_data.py --cleanup-generated")
    print("如需连同原始输入一起清理：python scripts\\manage_teacher_trial_data.py --cleanup-generated --cleanup-inputs --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
