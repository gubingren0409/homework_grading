"""
Phase 35 contract validation script (offline, deterministic).

Checks:
1) Every region has legal normalized bbox in [0,1] and monotonic boundaries.
2) Cognition feedback references only existing perception region anchors (UUID/ID binding).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_layout_contract(layout: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    regions = layout.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("LayoutIR regions missing or empty")

    for idx, region in enumerate(regions):
        if not isinstance(region, dict):
            raise ValueError(f"Region[{idx}] must be object")
        target_id = str(region.get("target_id") or "")
        if not target_id:
            raise ValueError(f"Region[{idx}] missing target_id")
        bbox = region.get("bbox")
        if not isinstance(bbox, dict):
            raise ValueError(f"Region[{idx}] bbox must be object")

        try:
            x_min = float(bbox["x_min"])
            y_min = float(bbox["y_min"])
            x_max = float(bbox["x_max"])
            y_max = float(bbox["y_max"])
        except Exception as exc:
            raise ValueError(f"Region[{idx}] bbox malformed: {exc}") from exc

        if not (0.0 <= x_min <= 1.0 and 0.0 <= y_min <= 1.0 and 0.0 <= x_max <= 1.0 and 0.0 <= y_max <= 1.0):
            raise ValueError(f"Region[{idx}] bbox out of [0,1] range")
        if x_max < x_min or y_max < y_min:
            raise ValueError(f"Region[{idx}] bbox non-monotonic")
        if x_max == x_min or y_max == y_min:
            warnings.append(f"Region[{idx}] degenerate bbox size")
    return warnings


def _assert_anchor_binding(layout: Dict[str, Any], cognition: Dict[str, Any]) -> None:
    regions = layout.get("regions", [])
    anchors = {str(r.get("target_id")) for r in regions if isinstance(r, dict)}

    steps = cognition.get("step_evaluations")
    if not isinstance(steps, list):
        raise ValueError("cognition.step_evaluations missing")

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"step_evaluations[{idx}] must be object")
        ref = str(step.get("reference_element_id") or "")
        if not ref:
            raise ValueError(f"step_evaluations[{idx}] missing reference_element_id")
        if ref not in anchors:
            raise ValueError(f"step_evaluations[{idx}] references unknown anchor: {ref}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Phase35 layout + anchor contracts.")
    parser.add_argument("--layout", required=True, type=str, help="Path to LayoutIR JSON")
    parser.add_argument("--cognition", required=True, type=str, help="Path to cognition report JSON")
    args = parser.parse_args()

    layout = _load_json(Path(args.layout))
    cognition = _load_json(Path(args.cognition))

    warnings = _assert_layout_contract(layout)
    _assert_anchor_binding(layout, cognition)

    print("PHASE35_CONTRACT=PASS")
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"- {w}")


if __name__ == "__main__":
    main()
