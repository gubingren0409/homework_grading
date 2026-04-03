#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence


def _run_pytest(expr: Sequence[str], cwd: Path) -> Dict[str, Any]:
    start = time.perf_counter()
    cmd = [sys.executable, "-m", "pytest", *expr]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
    )
    duration = time.perf_counter() - start
    return {
        "cmd": cmd,
        "exit_code": int(proc.returncode),
        "duration_seconds": duration,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _http_get_json(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    import requests

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return payload


def _start_uvicorn(cwd: Path, port: int) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    log_file = tempfile.NamedTemporaryFile(prefix="phasec-regression-", suffix=".log", delete=False)
    log_file_path = Path(log_file.name)
    log_file.close()
    stdout_handle = open(log_file_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=stdout_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    setattr(proc, "_phasec_log_path", str(log_file_path))
    setattr(proc, "_phasec_log_handle", stdout_handle)
    return proc


def _wait_server_ready(base_url: str, timeout_seconds: float = 30.0) -> None:
    import requests

    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # pragma: no cover
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"Server did not become ready within {timeout_seconds}s: {last_error}")


def _measure_api_latency(base_url: str, rounds: int) -> Dict[str, Any]:
    durations: List[float] = []
    payloads: List[Dict[str, Any]] = []
    for _ in range(rounds):
        start = time.perf_counter()
        payload = _http_get_json(f"{base_url}/api/v1/metrics/runtime-dashboard?window_hours=24")
        durations.append(time.perf_counter() - start)
        payloads.append(payload)
    durations_sorted = sorted(durations)
    p50 = durations_sorted[int((len(durations_sorted) - 1) * 0.50)]
    p95 = durations_sorted[int((len(durations_sorted) - 1) * 0.95)]
    return {
        "rounds": rounds,
        "latency_seconds_p50": p50,
        "latency_seconds_p95": p95,
        "latency_seconds_max": max(durations_sorted) if durations_sorted else 0.0,
        "sample_payload": payloads[-1] if payloads else {},
    }


def _assert_runtime_dashboard_shape(payload: Dict[str, Any]) -> None:
    required_keys = {"version", "window_hours", "provider_hits", "fallback_triggers", "prompt_cache_hits", "human_review_rate"}
    missing = sorted(k for k in required_keys if k not in payload)
    if missing:
        raise AssertionError(f"runtime dashboard payload missing keys: {missing}")


def _assert_dataset_pipeline_shape(payload: Dict[str, Any]) -> None:
    required_keys = {"version", "window_hours", "dataset_assets", "review_queue"}
    missing = sorted(k for k in required_keys if k not in payload)
    if missing:
        raise AssertionError(f"dataset pipeline payload missing keys: {missing}")


def _run_phasec_matrix(repo_root: Path, *, port: int, rounds: int) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}

    checks["contract_and_sse"] = _run_pytest(
        [
            "tests/test_api.py::test_dataset_pipeline_summary_endpoint",
            "tests/test_api.py::test_runtime_dashboard_endpoint",
            "tests/test_phase33_sse_pubsub.py",
            "-q",
        ],
        repo_root,
    )

    checks["status_and_router"] = _run_pytest(
        [
            "tests/test_phase40_status_monotonicity.py",
            "tests/test_runtime_router.py",
            "-q",
        ],
        repo_root,
    )

    checks["payload_limits"] = _run_pytest(
        [
            "tests/test_phase40_payload_limits.py",
            "-q",
        ],
        repo_root,
    )

    server = _start_uvicorn(repo_root, port=port)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_server_ready(base_url)
        dataset_payload = _http_get_json(f"{base_url}/api/v1/metrics/dataset-pipeline?window_hours=24")
        _assert_dataset_pipeline_shape(dataset_payload)
        dashboard_latency = _measure_api_latency(base_url, rounds=rounds)
        _assert_runtime_dashboard_shape(dashboard_latency["sample_payload"])
        checks["metrics_api_probe"] = {
            "exit_code": 0,
            "duration_seconds": 0.0,
            "dataset_pipeline": dataset_payload,
            "runtime_dashboard_latency": dashboard_latency,
        }
    finally:
        server.poll()
        if server.returncode is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        handle = getattr(server, "_phasec_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    ok = all(int(item.get("exit_code", 1)) == 0 for item in checks.values())
    return {"ok": ok, "checks": checks}


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run Phase C regression matrix automation")
    p.add_argument("--port", type=int, default=18080, help="Temporary local API port")
    p.add_argument("--rounds", type=int, default=5, help="Latency sampling rounds")
    p.add_argument(
        "--report",
        type=str,
        default="outputs/phasec_regression_matrix_report.json",
        help="Where to write matrix report JSON",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    report = _run_phasec_matrix(repo_root, port=args.port, rounds=args.rounds)
    report_path = (repo_root / args.report).resolve()
    _write_report(report_path, report)
    print(f"Phase C regression matrix report written to: {report_path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
