from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.config import settings


def _check_directory_writable(path: Path) -> tuple[bool, str]:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".trial_ready_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(path)
    except OSError as exc:
        return False, str(exc)


def _count_directory_items_and_bytes(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    items = 0
    total_bytes = 0
    for item in path.rglob("*"):
        items += 1
        if item.is_file():
            try:
                total_bytes += item.stat().st_size
            except OSError:
                pass
    return items, total_bytes


def _check_redis_connection() -> tuple[bool, str]:
    try:
        import redis

        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        response = client.ping()
        return bool(response), f"{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
    except Exception as exc:
        return False, str(exc)


def build_trial_readiness_report() -> dict[str, Any]:
    db_path = Path(settings.sqlite_db_path).resolve()
    uploads_path = settings.uploads_path.resolve()
    paper_crops_path = uploads_path / "paper_crops"
    env_path = Path(".env").resolve()

    db_dir_ok, db_dir_detail = _check_directory_writable(db_path.parent)
    uploads_ok, uploads_detail = _check_directory_writable(uploads_path)
    redis_ok, redis_detail = _check_redis_connection()
    crop_items, crop_bytes = _count_directory_items_and_bytes(paper_crops_path)
    local_fallback_ok = not (
        settings.allow_local_task_fallback
        and settings.deployment_environment in {"staging", "prod"}
    )

    checks = [
        {"name": "env_file", "ok": env_path.exists(), "detail": str(env_path)},
        {"name": "sqlite_directory", "ok": db_dir_ok, "detail": db_dir_detail},
        {"name": "uploads_directory", "ok": uploads_ok, "detail": uploads_detail},
        {
            "name": "paper_crops_directory",
            "ok": True,
            "detail": f"path={paper_crops_path}; items={crop_items}; bytes={crop_bytes}",
        },
        {"name": "redis", "ok": redis_ok, "detail": redis_detail},
        {
            "name": "qwen_keys",
            "ok": bool(settings.parsed_qwen_keys) or not settings.llm_egress_enabled,
            "detail": f"count={len(settings.parsed_qwen_keys)}",
        },
        {
            "name": "deepseek_keys",
            "ok": bool(settings.parsed_deepseek_keys) or not settings.llm_egress_enabled,
            "detail": f"count={len(settings.parsed_deepseek_keys)}",
        },
        {
            "name": "local_fallback_boundary",
            "ok": local_fallback_ok,
            "detail": (
                f"enabled(single-node only, environment={settings.deployment_environment})"
                if settings.allow_local_task_fallback
                else "disabled"
            ),
        },
        {
            "name": "sse_config",
            "ok": settings.sse_stream_timeout_seconds > settings.sse_heartbeat_interval_seconds,
            "detail": (
                f"timeout={settings.sse_stream_timeout_seconds}s "
                f"heartbeat={settings.sse_heartbeat_interval_seconds}s"
            ),
        },
    ]
    overall_ok = all(bool(item["ok"]) for item in checks)
    return {
        "ok": overall_ok,
        "deployment_environment": settings.deployment_environment,
        "sqlite_db_path": str(db_path),
        "uploads_path": str(uploads_path),
        "checks": checks,
        "notes": [
            "Local task fallback is only safe on a single API node.",
            "Disable local task fallback in staging/prod unless the deployment is explicitly single-instance.",
            "Generated crop artifacts live under uploads/paper_crops and should be cleaned with the teacher data manager or upload TTL process.",
            "Redis must stay available for Celery queueing and SSE status fan-out.",
            "Run sqlite backup before upgrades or compose restarts that may touch the database file.",
        ],
    }
