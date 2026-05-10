from pathlib import Path

import scripts.manage_teacher_trial_data as manager


def test_build_teacher_trial_data_summary_counts_items(monkeypatch, tmp_path):
    reference_dir = tmp_path / "reference"
    students_dir = tmp_path / "students"
    outputs_dir = tmp_path / "outputs"
    crop_dir = tmp_path / "uploads" / "paper_crops"
    for directory in (reference_dir, students_dir, outputs_dir, crop_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (reference_dir / "a.pdf").write_bytes(b"pdf")
    (students_dir / "student1.png").write_bytes(b"png")
    (outputs_dir / "summary.csv").write_text("ok", encoding="utf-8")
    (crop_dir / "c1.png").write_bytes(b"png")

    monkeypatch.setattr(manager, "REFERENCE_DIR", reference_dir)
    monkeypatch.setattr(manager, "STUDENTS_DIR", students_dir)
    monkeypatch.setattr(manager, "OUTPUTS_DIR", outputs_dir)
    monkeypatch.setattr(manager, "_generated_crop_dir", lambda: crop_dir)

    summary = manager.build_teacher_trial_data_summary()

    assert summary["reference_dir"]["items"] >= 1
    assert summary["students_dir"]["items"] >= 1
    assert summary["outputs_dir"]["items"] >= 1
    assert summary["generated_crop_dir"]["items"] >= 1


def test_reset_teacher_trial_data_keeps_directories_and_removes_contents(monkeypatch, tmp_path):
    reference_dir = tmp_path / "reference"
    students_dir = tmp_path / "students"
    outputs_dir = tmp_path / "outputs"
    crop_dir = tmp_path / "uploads" / "paper_crops"
    for directory in (reference_dir, students_dir, outputs_dir, crop_dir):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "artifact.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(manager, "REFERENCE_DIR", reference_dir)
    monkeypatch.setattr(manager, "STUDENTS_DIR", students_dir)
    monkeypatch.setattr(manager, "OUTPUTS_DIR", outputs_dir)
    monkeypatch.setattr(manager, "_generated_crop_dir", lambda: crop_dir)

    reset_paths = manager.reset_teacher_trial_data(remove_inputs=False)

    assert outputs_dir.resolve() in reset_paths
    assert crop_dir.resolve() in reset_paths
    assert not any(outputs_dir.iterdir())
    assert not any(crop_dir.iterdir())
    assert (reference_dir / "artifact.txt").exists()
    assert (students_dir / "artifact.txt").exists()


def test_reset_teacher_trial_data_can_remove_inputs_when_requested(monkeypatch, tmp_path):
    reference_dir = tmp_path / "reference"
    students_dir = tmp_path / "students"
    outputs_dir = tmp_path / "outputs"
    crop_dir = tmp_path / "uploads" / "paper_crops"
    for directory in (reference_dir, students_dir, outputs_dir, crop_dir):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "artifact.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(manager, "REFERENCE_DIR", reference_dir)
    monkeypatch.setattr(manager, "STUDENTS_DIR", students_dir)
    monkeypatch.setattr(manager, "OUTPUTS_DIR", outputs_dir)
    monkeypatch.setattr(manager, "_generated_crop_dir", lambda: crop_dir)

    manager.reset_teacher_trial_data(remove_inputs=True)

    assert not any(reference_dir.iterdir())
    assert not any(students_dir.iterdir())
