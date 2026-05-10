from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import teacher_paper_runner
from scripts.configure_teacher_trial import collect_teacher_trial_config_issues
from teacher_batch_runner import (
    OUTPUTS_DIR,
    REFERENCE_DIR,
    RUNTIME_ROOT,
    STUDENTS_DIR,
    SUPPORTED_EXTENSIONS,
    TRIAL_DIR,
)


def ensure_local_trial_workspace() -> bool:
    for directory in (TRIAL_DIR, REFERENCE_DIR, STUDENTS_DIR, OUTPUTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    env_path = RUNTIME_ROOT / ".env"
    env_example = ROOT / ".env.example"
    if env_path.exists() or not env_example.exists():
        return False
    shutil.copyfile(env_example, env_path)
    return True


def _directory_has_supported_files(directory: Path) -> bool:
    return any(
        entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS
        for entry in directory.iterdir()
    )


def _students_dir_has_submissions() -> bool:
    for entry in STUDENTS_DIR.iterdir():
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            return True
        if entry.is_dir() and _directory_has_supported_files(entry):
            return True
    return False


def collect_missing_inputs() -> list[str]:
    missing: list[str] = []
    if not _directory_has_supported_files(REFERENCE_DIR):
        missing.append(f"参考答案目录为空：{REFERENCE_DIR}")
    if not _students_dir_has_submissions():
        missing.append(f"学生作答目录为空：{STUDENTS_DIR}")
    return missing


def main() -> int:
    created_env = ensure_local_trial_workspace()

    print("=== 本地教师整卷试用启动器 ===")
    print(f"运行目录：{RUNTIME_ROOT}")
    print(f"试用目录：{TRIAL_DIR}")
    print(f"参考答案目录：{REFERENCE_DIR}")
    print(f"学生作答目录：{STUDENTS_DIR}")
    print(f"输出目录：{OUTPUTS_DIR}")
    print("")

    if created_env:
        print("已自动创建 .env，请先运行 configure_teacher_trial.bat 填写 API key 后再重试。")
        print(f"配置文件：{RUNTIME_ROOT / '.env'}")
        return 1

    config_issues = collect_teacher_trial_config_issues(RUNTIME_ROOT / ".env")
    if config_issues:
        print("启动前配置检查未通过：")
        for item in config_issues:
            print(f"- {item}")
        print("")
        print("请先运行 configure_teacher_trial.bat 完成配置后再重试。")
        return 1

    missing_inputs = collect_missing_inputs()
    if missing_inputs:
        print("启动前检查未通过：")
        for item in missing_inputs:
            print(f"- {item}")
        print("")
        print("请先把参考答案 PDF/图片放到 reference，把学生整卷 PDF/图片放到 students 后再重试。")
        print("如需查看当前本地数据目录，请运行 manage_teacher_trial_data.bat。")
        return 1

    return teacher_paper_runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
