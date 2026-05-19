import scripts.validate_teacher_trial_setup as validator


def test_validation_report_passes_without_inputs_when_config_is_ok(monkeypatch):
    monkeypatch.setattr(validator, "ensure_local_trial_workspace", lambda: False)
    monkeypatch.setattr(validator, "collect_teacher_trial_config_issues", lambda env_path: [])

    report = validator.build_teacher_trial_validation_report(require_inputs=False)

    assert report["ok"] is True
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["config_issues"]["ok"] is True
    assert report["checklist"] == ["运行 start_teacher_trial.bat 开始本地教师试用。"]


def test_validation_report_requires_inputs_when_requested(monkeypatch):
    monkeypatch.setattr(validator, "ensure_local_trial_workspace", lambda: False)
    monkeypatch.setattr(validator, "collect_teacher_trial_config_issues", lambda env_path: [])
    monkeypatch.setattr(validator, "collect_missing_inputs", lambda: ["学生作答目录为空"])

    report = validator.build_teacher_trial_validation_report(require_inputs=True)

    assert report["ok"] is False
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["input_ready"]["ok"] is False
    assert "学生作答目录为空" in checks["input_ready"]["detail"]
    assert report["checklist"] == [
        "把参考答案 PDF/图片放到 teacher_trial\\reference。",
        "把学生整卷 PDF/图片放到 teacher_trial\\students。",
    ]
