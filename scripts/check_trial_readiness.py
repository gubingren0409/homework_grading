from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.trial_readiness import build_trial_readiness_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Check single-server trial readiness.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    report = build_trial_readiness_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print("OK" if report["ok"] else "NOT_READY")
    for check in report["checks"]:
        status = "OK" if check["ok"] else "FAIL"
        print(f"[{status}] {check['name']}: {check['detail']}")
    for note in report["notes"]:
        print(f"- {note}")


if __name__ == "__main__":
    main()
