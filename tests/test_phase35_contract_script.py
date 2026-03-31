import json
import subprocess
import sys
from pathlib import Path


def test_phase35_contract_script_passes():
    root = Path(__file__).resolve().parent
    layout = root / "fixtures" / "phase35" / "layout_extreme_misalignment.json"
    cognition = root / "fixtures" / "phase35" / "cognition_anchor_valid.json"
    script = Path(__file__).resolve().parent.parent / "scripts" / "validate_phase35_contract.py"

    proc = subprocess.run(
        [sys.executable, str(script), "--layout", str(layout), "--cognition", str(cognition)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
    assert "PHASE35_CONTRACT=PASS" in proc.stdout


def test_phase35_contract_script_fails_on_bad_anchor(tmp_path: Path):
    root = Path(__file__).resolve().parent
    layout = root / "fixtures" / "phase35" / "layout_extreme_misalignment.json"
    bad_cognition = tmp_path / "bad_cognition.json"
    bad_cognition.write_text(
        json.dumps(
            {
                "status": "SCORED",
                "step_evaluations": [
                    {"reference_element_id": "not_exists", "is_correct": False, "error_type": "LOGIC"}
                ],
                "overall_feedback": "bad anchor",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parent.parent / "scripts" / "validate_phase35_contract.py"

    proc = subprocess.run(
        [sys.executable, str(script), "--layout", str(layout), "--cognition", str(bad_cognition)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
