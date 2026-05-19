from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RUNTIME_PROFILE_UPDATES: dict[str, dict[str, str]] = {
    "fast": {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "2",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "4",
        "PAPER_LAYOUT_ENABLED": "true",
    },
    "full": {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "1",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "2",
        "PAPER_LAYOUT_ENABLED": "true",
    },
    "fast-with-layout-disabled": {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "2",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "4",
        "PAPER_LAYOUT_ENABLED": "false",
    },
    "fast-with-strict-timeouts": {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "2",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "4",
        "PAPER_LAYOUT_ENABLED": "true",
        "PAPER_LAYOUT_TIMEOUT_SECONDS": "12",
        "PAPER_STUDENT_PAGE_OCR_TIMEOUT_SECONDS": "45",
        "PAPER_ANSWER_REGION_OCR_TIMEOUT_SECONDS": "45",
        "PAPER_COGNITIVE_TIMEOUT_SECONDS": "60",
    },
    "dev-smoke": {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "2",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "4",
        "PAPER_LAYOUT_ENABLED": "true",
        "PAPER_LAYOUT_TIMEOUT_SECONDS": "12",
        "PAPER_STUDENT_PAGE_OCR_TIMEOUT_SECONDS": "45",
        "PAPER_ANSWER_REGION_OCR_TIMEOUT_SECONDS": "45",
        "PAPER_COGNITIVE_TIMEOUT_SECONDS": "60",
    },
    "teacher-trial": {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "1",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "2",
        "PAPER_LAYOUT_ENABLED": "true",
    },
    "full-regression": {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "1",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "2",
        "PAPER_LAYOUT_ENABLED": "true",
    },
}


def load_env_lines(env_path: Path) -> list[str]:
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8").splitlines()


def parse_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in load_env_lines(env_path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    placeholder_markers = ("sk-your-", "your-", "change-me", "example", "demo-key")
    return any(marker in normalized for marker in placeholder_markers)


def collect_teacher_trial_config_issues(env_path: Path) -> list[str]:
    values = parse_env_values(env_path)
    issues: list[str] = []
    qwen = values.get("QWEN_API_KEYS", "")
    deepseek = values.get("DEEPSEEK_API_KEYS", "")
    llm_egress = values.get("LLM_EGRESS_ENABLED", "true").strip().lower()

    if is_placeholder_secret(qwen):
        issues.append("QWEN_API_KEYS 未填写真实可用的 key。")
    if is_placeholder_secret(deepseek):
        issues.append("DEEPSEEK_API_KEYS 未填写真实可用的 key。")
    if llm_egress != "true":
        issues.append("LLM_EGRESS_ENABLED 当前不是 true，模型外呼会被阻断。")
    return issues


def resolve_runtime_profile_updates(profile: str | None) -> dict[str, str]:
    if profile is None:
        return {}
    return dict(RUNTIME_PROFILE_UPDATES[profile])


def upsert_env_values(env_path: Path, updates: dict[str, str]) -> None:
    lines = load_env_lines(env_path)
    remaining = dict(updates)
    output: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key, _ = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in remaining:
            output.append(f"{normalized_key}={remaining.pop(normalized_key)}")
        else:
            output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(output).strip() + "\n", encoding="utf-8")


def ensure_env_file(env_path: Path, env_example_path: Path) -> bool:
    if env_path.exists() or not env_example_path.exists():
        return False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(env_example_path, env_path)
    return True


def _prompt_or_keep(label: str, current: str) -> str:
    prompt = f"{label}"
    if current:
        prompt = f"{prompt}（直接回车保留当前值）"
    prompt = f"{prompt}: "
    entered = input(prompt).strip()
    return entered or current


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure local teacher trial API keys.")
    parser.add_argument("--qwen", default=None, help="Qwen API keys, comma-separated.")
    parser.add_argument("--deepseek", default=None, help="DeepSeek API keys, comma-separated.")
    parser.add_argument(
        "--runtime-profile",
        choices=sorted(RUNTIME_PROFILE_UPDATES.keys()),
        default=None,
        help="Apply OCR throughput preset: fast for quick smoke, full for conservative full runs.",
    )
    parser.add_argument(
        "--llm-egress-enabled",
        choices=["true", "false"],
        default=None,
        help="Whether to allow model egress.",
    )
    args = parser.parse_args()

    env_path = ROOT / ".env"
    env_example_path = ROOT / ".env.example"
    created = ensure_env_file(env_path, env_example_path)
    current = parse_env_values(env_path)

    qwen = args.qwen if args.qwen is not None else _prompt_or_keep("请输入 QWEN_API_KEYS", current.get("QWEN_API_KEYS", ""))
    deepseek = (
        args.deepseek
        if args.deepseek is not None
        else _prompt_or_keep("请输入 DEEPSEEK_API_KEYS", current.get("DEEPSEEK_API_KEYS", ""))
    )
    llm_egress = (
        args.llm_egress_enabled
        if args.llm_egress_enabled is not None
        else _prompt_or_keep("是否允许模型外呼（true/false）", current.get("LLM_EGRESS_ENABLED", "true")).lower()
    )
    runtime_updates = resolve_runtime_profile_updates(args.runtime_profile)

    updates = {
        "QWEN_API_KEYS": qwen,
        "DEEPSEEK_API_KEYS": deepseek,
        "LLM_EGRESS_ENABLED": llm_egress,
    }
    updates.update(runtime_updates)
    upsert_env_values(env_path, updates)

    print("本地教师版配置已更新。")
    print(f"配置文件：{env_path}")
    if created:
        print("已根据 .env.example 创建新的 .env。")
    if args.runtime_profile:
        print(
            "已应用运行模式："
            f"{args.runtime_profile}"
            f"（QWEN_BATCH_MAX_IMAGES={runtime_updates['QWEN_BATCH_MAX_IMAGES']}, "
            f"QWEN_SINGLE_IMAGE_CONCURRENCY={runtime_updates['QWEN_SINGLE_IMAGE_CONCURRENCY']}, "
            f"QWEN_ANSWER_REGION_BATCH_CONCURRENCY={runtime_updates['QWEN_ANSWER_REGION_BATCH_CONCURRENCY']}）"
        )

    issues = collect_teacher_trial_config_issues(env_path)
    if issues:
        print("仍有配置问题：")
        for item in issues:
            print(f"- {item}")
        return 1

    print("关键配置检查通过，可以继续运行 start_teacher_trial.bat。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
