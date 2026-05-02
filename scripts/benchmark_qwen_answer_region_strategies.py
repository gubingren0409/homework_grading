from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.cognitive.mock_agent import MockCognitiveAgent
from src.core.config import settings
from src.orchestration.paper_workflow import PaperGradingWorkflow
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine


DEFAULT_CROPS = [
    r"..\output\student_whole_paper_qwen_review\student_crops\region_017_17.jpg",
    r"..\output\student_whole_paper_qwen_review\student_crops\region_018_18.jpg",
    r"..\output\student_whole_paper_qwen_review\student_crops\region_019_19.jpg",
    r"..\output\student_whole_paper_qwen_review\student_crops\region_020_20.jpg",
]


STRATEGIES = [
    {"name": "single_concurrent_2", "batch_size": 1, "batch_concurrency": 2, "single_concurrency": 2},
    {"name": "batch_2_concurrent_2", "batch_size": 2, "batch_concurrency": 2, "single_concurrency": 1},
    {"name": "batch_3_concurrent_2", "batch_size": 3, "batch_concurrency": 2, "single_concurrency": 1},
    {"name": "batch_all_once", "batch_size": 999, "batch_concurrency": 1, "single_concurrency": 1},
]


def _resolve_crop_paths(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        paths.append(path)
    return paths


async def _run_strategy(paths: list[Path], strategy: dict[str, Any]) -> dict[str, Any]:
    settings.qwen_answer_region_strategy = "fixed"
    settings.qwen_batch_max_images = int(strategy["batch_size"])
    settings.qwen_answer_region_batch_concurrency = int(strategy["batch_concurrency"])
    settings.qwen_single_image_concurrency = int(strategy["single_concurrency"])

    workflow = PaperGradingWorkflow(
        perception_engine=QwenVLMPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )
    prepared_images = [
        workflow._prepare_answer_region_image(path.read_bytes())
        for path in paths
    ]

    started = time.perf_counter()
    status = "ok"
    error = None
    outputs_count = 0
    warnings: list[str] = []
    student_tagged_outputs = 0
    try:
        outputs = await workflow._process_images_in_chunks(
            prepared_images,
            context_type="student_answer_regions",
        )
        warnings = workflow._drain_perception_fallback_warnings()
        outputs_count = len(outputs)
        student_tagged_outputs = sum(
            1
            for output in outputs
            if any("<student>" in element.raw_content for element in output.elements)
        )
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    return {
        "name": strategy["name"],
        "batch_size": strategy["batch_size"],
        "batch_concurrency": strategy["batch_concurrency"],
        "single_concurrency": strategy["single_concurrency"],
        "elapsed_seconds": round(elapsed, 2),
        "status": status,
        "error": error,
        "outputs_count": outputs_count,
        "student_tagged_outputs": student_tagged_outputs,
        "warnings": warnings,
    }


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crops", nargs="*", default=DEFAULT_CROPS)
    parser.add_argument("--output", default=r"..\output\qwen_strategy_benchmark\answer_region_strategies.json")
    parser.add_argument("--strategy", choices=[strategy["name"] for strategy in STRATEGIES], action="append")
    parser.add_argument("--strategy-timeout-seconds", type=float, default=480.0)
    args = parser.parse_args()

    paths = _resolve_crop_paths(args.crops)
    results = []
    selected_strategies = [
        strategy for strategy in STRATEGIES
        if not args.strategy or strategy["name"] in args.strategy
    ]
    for strategy in selected_strategies:
        print(f"running {strategy['name']}...", flush=True)
        try:
            result = await asyncio.wait_for(
                _run_strategy(paths, strategy),
                timeout=args.strategy_timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = {
                "name": strategy["name"],
                "batch_size": strategy["batch_size"],
                "batch_concurrency": strategy["batch_concurrency"],
                "single_concurrency": strategy["single_concurrency"],
                "elapsed_seconds": args.strategy_timeout_seconds,
                "status": "timed_out",
                "error": f"strategy exceeded {args.strategy_timeout_seconds}s",
                "outputs_count": 0,
                "student_tagged_outputs": 0,
                "warnings": [],
            }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (REPO_ROOT / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "input_images": [str(path) for path in paths],
        "qwen_timeout_seconds": settings.qwen_api_timeout_seconds,
        "qwen_batch_timeout_seconds": settings.qwen_batch_api_timeout_seconds,
        "qwen_max_retries": settings.qwen_max_retries,
        "qwen_max_connection_errors": settings.qwen_max_connection_errors,
        "results": results,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
