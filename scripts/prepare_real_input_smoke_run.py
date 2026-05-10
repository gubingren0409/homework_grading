from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path


def create_smoke_run_template(base_dir: Path, run_name: str | None = None) -> Path:
    run_id = run_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    template_path = run_dir / "result_template.csv"
    with template_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "sample",
                "task_completed",
                "question_count_reasonable",
                "crop_review_ok",
                "review_reason_ok",
                "misrouting_observed",
                "export_ok",
                "notes",
            ],
        )
        writer.writeheader()

    checklist_path = run_dir / "checklist.txt"
    checklist_path.write_text(
        "\n".join(
            [
                "阶段性真实输入 smoke 检查项：",
                "1. 任务是否成功完成。",
                "2. 题号数量是否明显偏差。",
                "3. 每题实际评分 crop 是否能回看。",
                "4. review_reasons 是否符合肉眼观察。",
                "5. 是否出现明显漏题、错题归属或跨页归属错误。",
                "6. JSON/CSV/Markdown 报告是否完整。",
                "7. API/Worker/Redis/文件路径日志是否出现新异常。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a real-input smoke run template.")
    parser.add_argument(
        "--base-dir",
        default="teacher_trial\\smoke_runs",
        help="Directory where smoke run templates should be created.",
    )
    parser.add_argument("--run-name", default=None, help="Optional fixed run directory name.")
    args = parser.parse_args()

    run_dir = create_smoke_run_template(Path(args.base_dir), args.run_name)
    print(run_dir)
    print(run_dir / "result_template.csv")
    print(run_dir / "checklist.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
