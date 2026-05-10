from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.configure_teacher_trial import collect_teacher_trial_config_issues
from scripts.launch_teacher_trial import collect_missing_inputs, ensure_local_trial_workspace
from teacher_batch_runner import OUTPUTS_DIR, REFERENCE_DIR, RUNTIME_ROOT, STUDENTS_DIR, TRIAL_DIR


def build_teacher_trial_validation_report(*, require_inputs: bool = False) -> dict[str, Any]:
    created_env = ensure_local_trial_workspace()
    env_path = RUNTIME_ROOT / ".env"
    config_issues = collect_teacher_trial_config_issues(env_path) if env_path.exists() else ["未找到 .env 配置文件。"]
    input_issues = collect_missing_inputs() if require_inputs else []

    checks = [
        {"name": "runtime_root", "ok": RUNTIME_ROOT.exists(), "detail": str(RUNTIME_ROOT)},
        {"name": "trial_dir", "ok": TRIAL_DIR.exists(), "detail": str(TRIAL_DIR)},
        {"name": "reference_dir", "ok": REFERENCE_DIR.exists(), "detail": str(REFERENCE_DIR)},
        {"name": "students_dir", "ok": STUDENTS_DIR.exists(), "detail": str(STUDENTS_DIR)},
        {"name": "outputs_dir", "ok": OUTPUTS_DIR.exists(), "detail": str(OUTPUTS_DIR)},
        {"name": "env_file", "ok": env_path.exists(), "detail": str(env_path)},
        {"name": "config_issues", "ok": not config_issues, "detail": "；".join(config_issues) or "ok"},
    ]
    if require_inputs:
        checks.append(
            {
                "name": "input_ready",
                "ok": not input_issues,
                "detail": "；".join(input_issues) or "ok",
            }
        )

    return {
        "ok": all(bool(item["ok"]) for item in checks),
        "created_env": created_env,
        "require_inputs": require_inputs,
        "checks": checks,
        "notes": [
            "默认校验只检查本地教师版启动条件，不强制要求已放入真实样例。",
            "使用 --require-inputs 可以在阶段验收时把参考卷/学生卷目录也纳入校验。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate local teacher trial setup.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--require-inputs",
        action="store_true",
        help="Also require teacher_trial/reference and teacher_trial/students to contain files.",
    )
    args = parser.parse_args()

    report = build_teacher_trial_validation_report(require_inputs=args.require_inputs)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("OK" if report["ok"] else "NOT_READY")
        for check in report["checks"]:
            status = "OK" if check["ok"] else "FAIL"
            print(f"[{status}] {check['name']}: {check['detail']}")
        for note in report["notes"]:
            print(f"- {note}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
