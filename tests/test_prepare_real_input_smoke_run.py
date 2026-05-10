from pathlib import Path

from scripts.prepare_real_input_smoke_run import create_smoke_run_template


def test_create_smoke_run_template_creates_csv_and_checklist(tmp_path):
    run_dir = create_smoke_run_template(tmp_path, "smoke-001")

    csv_path = run_dir / "result_template.csv"
    checklist_path = run_dir / "checklist.txt"

    assert run_dir == tmp_path / "smoke-001"
    assert csv_path.exists()
    assert checklist_path.exists()
    assert "sample,task_completed,question_count_reasonable" in csv_path.read_text(encoding="utf-8-sig")
    assert "阶段性真实输入 smoke 检查项：" in checklist_path.read_text(encoding="utf-8")
