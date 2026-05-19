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


def _build_trial_readiness_checklist(checks: list[dict[str, object]]) -> list[str]:
    failed = {str(item["name"]) for item in checks if not bool(item["ok"])}
    checklist: list[str] = []
    if "env_file" in failed:
        checklist.append("补齐 .env，并确认模型 key、Redis、SQLite 路径配置正确。")
    if "auth_boundary" in failed:
        checklist.append("在 staging/prod 开启 AUTH_ENABLED，避免单机演示环境误暴露匿名访问。")
    if "redis" in failed:
        checklist.append("启动 Redis，并确认 Celery / SSE 依赖的 redis_host、redis_port、redis_db 可连通。")
    if "local_fallback_boundary" in failed:
        checklist.append("在 staging/prod 关闭 ALLOW_LOCAL_TASK_FALLBACK，或明确当前部署仅为单实例。")
    if "sqlite_backup_directory" in failed:
        checklist.append("确保 SQLite 所在目录可写，并在升级前先执行数据库备份。")
    if "uploads_retention" in failed:
        checklist.append("设置 UPLOAD_TTL_DAYS 为正数，避免 uploads / paper_crops 无上限增长。")
    if "sse_config" in failed:
        checklist.append("确保 SSE_STREAM_TIMEOUT_SECONDS 大于 SSE_HEARTBEAT_INTERVAL_SECONDS。")
    if not checklist:
        checklist.append("上线前再次执行 check_trial_readiness.bat，并先做 SQLite 备份。")
    return checklist


def build_trial_readiness_report() -> dict[str, Any]:
    db_path = Path(settings.sqlite_db_path).resolve()
    uploads_path = settings.uploads_path.resolve()
    paper_crops_path = uploads_path / "paper_crops"
    env_path = Path(".env").resolve()

    db_dir_ok, db_dir_detail = _check_directory_writable(db_path.parent)
    backup_dir_ok, backup_dir_detail = _check_directory_writable(db_path.parent)
    uploads_ok, uploads_detail = _check_directory_writable(uploads_path)
    redis_ok, redis_detail = _check_redis_connection()
    crop_items, crop_bytes = _count_directory_items_and_bytes(paper_crops_path)
    local_fallback_ok = not (
        settings.allow_local_task_fallback
        and settings.deployment_environment in {"staging", "prod"}
    )
    auth_boundary_ok = settings.deployment_environment == "dev" or settings.auth_enabled

    checks = [
        {"name": "env_file", "ok": env_path.exists(), "detail": str(env_path)},
        {
            "name": "auth_boundary",
            "ok": auth_boundary_ok,
            "detail": (
                f"enabled(environment={settings.deployment_environment})"
                if settings.auth_enabled
                else f"disabled(environment={settings.deployment_environment})"
            ),
        },
        {"name": "sqlite_directory", "ok": db_dir_ok, "detail": db_dir_detail},
        {
            "name": "sqlite_backup_directory",
            "ok": backup_dir_ok,
            "detail": f"{backup_dir_detail}; suggested_backup={db_path.with_suffix('.backup.sqlite3')}",
        },
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
        {
            "name": "uploads_retention",
            "ok": settings.upload_ttl_days > 0,
            "detail": f"upload_ttl_days={settings.upload_ttl_days}",
        },
    ]
    overall_ok = all(bool(item["ok"]) for item in checks)
    return {
        "ok": overall_ok,
        "deployment_environment": settings.deployment_environment,
        "sqlite_db_path": str(db_path),
        "uploads_path": str(uploads_path),
        "checks": checks,
        "checklist": _build_trial_readiness_checklist(checks),
        "notes": [
            "Local task fallback is only safe on a single API node.",
            "Disable local task fallback in staging/prod unless the deployment is explicitly single-instance.",
            "Generated crop artifacts live under uploads/paper_crops and should be cleaned with the teacher data manager or upload TTL process.",
            "Redis must stay available for Celery queueing and SSE status fan-out.",
            "Run sqlite backup before upgrades or compose restarts that may touch the database file.",
        ],
    }
