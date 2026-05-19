from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cognitive.mock_agent import MockCognitiveAgent
from src.core.config import settings
from src.orchestration.paper_workflow import PaperGradingWorkflow
from src.perception.factory import create_perception_engine
from src.skills.service import SkillService

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}


def collect_layout_inputs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"input path not found: {path}")
    files = sorted(
        item for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not files:
        raise ValueError(f"no supported files found under {path}")
    return files


def setting_overrides_for_mode(mode: str, *, timeout_seconds: float) -> dict[str, object]:
    if mode == "enabled":
        return {
            "paper_layout_enabled": True,
        }
    if mode == "disabled":
        return {
            "paper_layout_enabled": False,
        }
    if mode == "timeout-fallback":
        return {
            "paper_layout_enabled": True,
            "paper_layout_timeout_seconds": timeout_seconds,
        }
    raise ValueError(f"unsupported mode: {mode}")


@contextmanager
def apply_setting_overrides(overrides: dict[str, object]) -> Iterator[None]:
    previous = {key: getattr(settings, key) for key in overrides}
    try:
        for key, value in overrides.items():
            setattr(settings, key, value)
        yield
    finally:
        for key, value in previous.items():
            setattr(settings, key, value)


async def benchmark_layout_tail(
    *,
    input_paths: list[Path],
    rounds: int,
    modes: list[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    perception_engine = create_perception_engine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=MockCognitiveAgent(),
        skill_service=SkillService(),
    )
    runs: list[dict[str, Any]] = []
    for mode in modes:
        overrides = setting_overrides_for_mode(mode, timeout_seconds=timeout_seconds)
        with apply_setting_overrides(overrides):
            for round_index in range(1, rounds + 1):
                for input_path in input_paths:
                    runtime_profile = workflow._new_runtime_profile()
                    layout_result = await workflow._parse_layout_with_runtime(
                        input_path.read_bytes(),
                        page_index=0,
                        runtime_profile=runtime_profile,
                    )
                    layout_span = next(
                        (
                            span
                            for span in runtime_profile.get("spans", [])
                            if isinstance(span, dict) and span.get("stage") == "student_page_layout"
                        ),
                        {},
                    )
                    runs.append(
                        {
                            "mode": mode,
                            "round": round_index,
                            "input_path": str(input_path),
                            "region_count": len(layout_result.regions),
                            "warnings": list(layout_result.warnings),
                            "elapsed_seconds": float(layout_span.get("elapsed_seconds") or 0.0),
                            "provider": layout_span.get("provider"),
                            "fallback_from": layout_span.get("fallback_from"),
                            "fallback_to": layout_span.get("fallback_to"),
                            "error_types": list(layout_span.get("error_types") or []),
                        }
                    )
    return summarize_layout_runs(runs)


def summarize_layout_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        grouped.setdefault(str(run.get("mode") or "unknown"), []).append(run)

    return {
        "run_count": len(runs),
        "modes": [
            {
                "mode": mode,
                "count": len(items),
                "avg_seconds": round(sum(float(item.get("elapsed_seconds") or 0.0) for item in items) / len(items), 3),
                "p95_seconds": round(_percentile([float(item.get("elapsed_seconds") or 0.0) for item in items], 0.95), 3),
                "max_seconds": round(max(float(item.get("elapsed_seconds") or 0.0) for item in items), 3),
                "avg_region_count": round(sum(int(item.get("region_count") or 0) for item in items) / len(items), 3),
                "error_type_hits": _count_strings(
                    error_type
                    for item in items
                    for error_type in (item.get("error_types") or [])
                ),
                "warning_hits": _count_strings(
                    warning
                    for item in items
                    for warning in (item.get("warnings") or [])
                ),
            }
            for mode, items in sorted(grouped.items())
        ],
        "runs": runs,
    }


def _count_strings(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    target = max(0.0, min(1.0, ratio)) * (len(ordered) - 1)
    lower = int(target)
    upper = min(len(ordered) - 1, lower + 1)
    weight = target - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark layout-tail latency across enabled/disabled/timeout-fallback modes.")
    parser.add_argument("--input", required=True, help="Single image/pdf path or a directory of inputs.")
    parser.add_argument("--rounds", type=int, default=3, help="How many rounds to repeat per mode.")
    parser.add_argument(
        "--mode",
        action="append",
        choices=["enabled", "disabled", "timeout-fallback"],
        help="Modes to run. Defaults to all.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=0.01,
        help="Timeout used for timeout-fallback mode.",
    )
    parser.add_argument("--output-json", default=None, help="Optional file path for JSON output.")
    args = parser.parse_args()

    summary = asyncio.run(
        benchmark_layout_tail(
            input_paths=collect_layout_inputs(Path(args.input)),
            rounds=max(1, int(args.rounds)),
            modes=args.mode or ["enabled", "disabled", "timeout-fallback"],
            timeout_seconds=float(args.timeout_seconds),
        )
    )
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        print(output_path)
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
