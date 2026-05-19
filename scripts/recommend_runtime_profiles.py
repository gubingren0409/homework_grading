from __future__ import annotations

import argparse
import json
from typing import Any

from scripts.configure_teacher_trial import resolve_runtime_profile_updates


def build_runtime_profile_recommendations() -> dict[str, Any]:
    return {
        "decision_basis": [
            "fast 目前不能直接设为真实整卷默认。",
            "teacher_trial 优先稳定性与可解释复核。",
            "dev_smoke 优先较短等待时间和快速暴露长尾。",
            "full_regression 保持保守并发，作为稳定回归基线。",
        ],
        "profiles": {
            "dev_smoke": {
                "source_profile": "dev-smoke",
                "recommended_for": "开发期快速冒烟和长尾复现",
                "env_updates": resolve_runtime_profile_updates("dev-smoke"),
            },
            "teacher_trial": {
                "source_profile": "teacher-trial",
                "recommended_for": "教师本地试用与最小真实整卷运行",
                "env_updates": resolve_runtime_profile_updates("teacher-trial"),
            },
            "full_regression": {
                "source_profile": "full-regression",
                "recommended_for": "完整回归、结果稳定性基线",
                "env_updates": resolve_runtime_profile_updates("full-regression"),
            },
        },
    }


def render_markdown(recommendations: dict[str, Any]) -> str:
    lines = [
        "# Runtime profile recommendations",
        "",
        "## Decision basis",
        "",
    ]
    for item in recommendations.get("decision_basis", []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Profiles",
            "",
            "| profile | source_profile | recommended_for | env_updates |",
            "| --- | --- | --- | --- |",
        ]
    )
    for profile_name, payload in recommendations.get("profiles", {}).items():
        lines.append(
            f"| {profile_name} | {payload.get('source_profile')} | {payload.get('recommended_for')} | "
            f"{json.dumps(payload.get('env_updates') or {}, ensure_ascii=False)} |"
        )
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current recommended runtime profiles.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    args = parser.parse_args()

    recommendations = build_runtime_profile_recommendations()
    if args.json:
        print(json.dumps(recommendations, ensure_ascii=False, indent=2))
        return
    print(render_markdown(recommendations))


if __name__ == "__main__":
    main()
