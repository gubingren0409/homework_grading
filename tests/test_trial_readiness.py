import sqlite3

import pytest

from src.utils.sqlite_ops import backup_sqlite_database, restore_sqlite_database
from src.utils.trial_readiness import build_trial_readiness_report


def _seed_sqlite(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO sample (name) VALUES (?)", ("demo",))
        conn.commit()


def test_sqlite_backup_and_restore_round_trip(tmp_path):
    db_path = tmp_path / "live.db"
    _seed_sqlite(db_path)

    backup_path = backup_sqlite_database(str(db_path))
    assert backup_path.exists()

    restored_path = tmp_path / "restored.db"
    result_path = restore_sqlite_database(str(backup_path), str(restored_path))

    assert result_path == restored_path.resolve()
    with sqlite3.connect(restored_path) as conn:
        row = conn.execute("SELECT name FROM sample").fetchone()
    assert row == ("demo",)


def test_sqlite_restore_requires_force_when_target_exists(tmp_path):
    backup_path = tmp_path / "backup.db"
    _seed_sqlite(backup_path)
    target_path = tmp_path / "target.db"
    _seed_sqlite(target_path)

    with pytest.raises(FileExistsError):
        restore_sqlite_database(str(backup_path), str(target_path))


def test_trial_readiness_report_uses_health_checks(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("AUTH_ENABLED=false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.utils.trial_readiness.settings.sqlite_db_path", str(tmp_path / "outputs" / "grading.db"))
    monkeypatch.setattr("src.utils.trial_readiness.settings.uploads_dir", str(tmp_path / "data" / "uploads"))
    monkeypatch.setattr("src.utils.trial_readiness.settings.qwen_api_keys", "sk-qwen")
    monkeypatch.setattr("src.utils.trial_readiness.settings.deepseek_api_keys", "sk-deepseek")
    monkeypatch.setattr("src.utils.trial_readiness.settings.allow_local_task_fallback", True)
    monkeypatch.setattr("src.utils.trial_readiness.settings.deployment_environment", "dev")
    monkeypatch.setattr("src.utils.trial_readiness.settings.auth_enabled", False)
    monkeypatch.setattr("src.utils.trial_readiness.settings.llm_egress_enabled", True)
    monkeypatch.setattr("src.utils.trial_readiness.settings.upload_ttl_days", 7)
    monkeypatch.setattr("src.utils.trial_readiness._check_redis_connection", lambda: (True, "redis-ok"))

    report = build_trial_readiness_report()

    assert report["ok"] is True
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["env_file"]["ok"] is True
    assert checks["auth_boundary"]["ok"] is True
    assert checks["redis"]["detail"] == "redis-ok"
    assert checks["paper_crops_directory"]["detail"].startswith("path=")
    assert checks["local_fallback_boundary"]["detail"] == "enabled(single-node only, environment=dev)"
    assert report["checklist"] == ["上线前再次执行 check_trial_readiness.bat，并先做 SQLite 备份。"]


def test_trial_readiness_flags_local_fallback_outside_dev(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("AUTH_ENABLED=true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.utils.trial_readiness.settings.sqlite_db_path", str(tmp_path / "outputs" / "grading.db"))
    monkeypatch.setattr("src.utils.trial_readiness.settings.uploads_dir", str(tmp_path / "data" / "uploads"))
    monkeypatch.setattr("src.utils.trial_readiness.settings.qwen_api_keys", "sk-qwen")
    monkeypatch.setattr("src.utils.trial_readiness.settings.deepseek_api_keys", "sk-deepseek")
    monkeypatch.setattr("src.utils.trial_readiness.settings.allow_local_task_fallback", True)
    monkeypatch.setattr("src.utils.trial_readiness.settings.deployment_environment", "staging")
    monkeypatch.setattr("src.utils.trial_readiness.settings.auth_enabled", True)
    monkeypatch.setattr("src.utils.trial_readiness.settings.llm_egress_enabled", True)
    monkeypatch.setattr("src.utils.trial_readiness.settings.upload_ttl_days", 7)
    monkeypatch.setattr("src.utils.trial_readiness._check_redis_connection", lambda: (True, "redis-ok"))

    report = build_trial_readiness_report()

    checks = {item["name"]: item for item in report["checks"]}
    assert report["ok"] is False
    assert checks["local_fallback_boundary"]["ok"] is False
    assert "关闭 ALLOW_LOCAL_TASK_FALLBACK" in report["checklist"][0]


def test_trial_readiness_flags_auth_and_upload_retention_in_prod(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("AUTH_ENABLED=false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.utils.trial_readiness.settings.sqlite_db_path", str(tmp_path / "outputs" / "grading.db"))
    monkeypatch.setattr("src.utils.trial_readiness.settings.uploads_dir", str(tmp_path / "data" / "uploads"))
    monkeypatch.setattr("src.utils.trial_readiness.settings.qwen_api_keys", "sk-qwen")
    monkeypatch.setattr("src.utils.trial_readiness.settings.deepseek_api_keys", "sk-deepseek")
    monkeypatch.setattr("src.utils.trial_readiness.settings.allow_local_task_fallback", False)
    monkeypatch.setattr("src.utils.trial_readiness.settings.deployment_environment", "prod")
    monkeypatch.setattr("src.utils.trial_readiness.settings.auth_enabled", False)
    monkeypatch.setattr("src.utils.trial_readiness.settings.llm_egress_enabled", True)
    monkeypatch.setattr("src.utils.trial_readiness.settings.upload_ttl_days", 0)
    monkeypatch.setattr("src.utils.trial_readiness._check_redis_connection", lambda: (True, "redis-ok"))

    report = build_trial_readiness_report()

    checks = {item["name"]: item for item in report["checks"]}
    assert report["ok"] is False
    assert checks["auth_boundary"]["ok"] is False
    assert checks["uploads_retention"]["ok"] is False
    assert any("开启 AUTH_ENABLED" in item for item in report["checklist"])
    assert any("UPLOAD_TTL_DAYS" in item for item in report["checklist"])
