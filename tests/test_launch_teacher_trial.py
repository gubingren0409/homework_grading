from pathlib import Path

import scripts.launch_teacher_trial as launcher


def test_ensure_local_trial_workspace_copies_env(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    trial_dir = runtime_root / "teacher_trial"
    reference_dir = trial_dir / "reference"
    students_dir = trial_dir / "students"
    outputs_dir = trial_dir / "outputs"
    env_example = tmp_path / ".env.example"
    env_example.write_text("QWEN_API_KEYS=test\n", encoding="utf-8")

    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(launcher, "TRIAL_DIR", trial_dir)
    monkeypatch.setattr(launcher, "REFERENCE_DIR", reference_dir)
    monkeypatch.setattr(launcher, "STUDENTS_DIR", students_dir)
    monkeypatch.setattr(launcher, "OUTPUTS_DIR", outputs_dir)

    created = launcher.ensure_local_trial_workspace()

    assert created is True
    assert (runtime_root / ".env").exists()
    assert reference_dir.is_dir()
    assert students_dir.is_dir()
    assert outputs_dir.is_dir()


def test_collect_missing_inputs_reports_empty_reference_and_students(monkeypatch, tmp_path):
    reference_dir = tmp_path / "reference"
    students_dir = tmp_path / "students"
    reference_dir.mkdir()
    students_dir.mkdir()

    monkeypatch.setattr(launcher, "REFERENCE_DIR", reference_dir)
    monkeypatch.setattr(launcher, "STUDENTS_DIR", students_dir)

    missing = launcher.collect_missing_inputs()

    assert len(missing) == 2
    assert "参考答案目录为空" in missing[0]
    assert "学生作答目录为空" in missing[1]


def test_collect_missing_inputs_accepts_nested_student_submission(monkeypatch, tmp_path):
    reference_dir = tmp_path / "reference"
    students_dir = tmp_path / "students"
    reference_dir.mkdir()
    students_dir.mkdir()
    (reference_dir / "answer.pdf").write_bytes(b"pdf")
    student_dir = students_dir / "student-a"
    student_dir.mkdir()
    (student_dir / "paper.png").write_bytes(b"png")

    monkeypatch.setattr(launcher, "REFERENCE_DIR", reference_dir)
    monkeypatch.setattr(launcher, "STUDENTS_DIR", students_dir)

    missing = launcher.collect_missing_inputs()

    assert missing == []
