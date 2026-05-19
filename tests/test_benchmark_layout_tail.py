from scripts.benchmark_layout_tail import setting_overrides_for_mode, summarize_layout_runs


def test_setting_overrides_for_mode_returns_expected_flags():
    assert setting_overrides_for_mode("enabled", timeout_seconds=0.5) == {
        "paper_layout_enabled": True,
    }
    assert setting_overrides_for_mode("disabled", timeout_seconds=0.5) == {
        "paper_layout_enabled": False,
    }
    assert setting_overrides_for_mode("timeout-fallback", timeout_seconds=0.5) == {
        "paper_layout_enabled": True,
        "paper_layout_timeout_seconds": 0.5,
    }


def test_summarize_layout_runs_groups_by_mode():
    summary = summarize_layout_runs(
        [
            {
                "mode": "enabled",
                "round": 1,
                "input_path": "a.png",
                "region_count": 3,
                "warnings": [],
                "elapsed_seconds": 2.0,
                "error_types": [],
            },
            {
                "mode": "enabled",
                "round": 2,
                "input_path": "a.png",
                "region_count": 4,
                "warnings": [],
                "elapsed_seconds": 4.0,
                "error_types": ["network"],
            },
            {
                "mode": "timeout-fallback",
                "round": 1,
                "input_path": "a.png",
                "region_count": 0,
                "warnings": ["LAYOUT_TIMEOUT_REVIEW"],
                "elapsed_seconds": 0.02,
                "error_types": ["timeout"],
            },
        ]
    )

    assert summary["run_count"] == 3
    enabled = next(item for item in summary["modes"] if item["mode"] == "enabled")
    timeout_mode = next(item for item in summary["modes"] if item["mode"] == "timeout-fallback")
    assert enabled["avg_seconds"] == 3.0
    assert enabled["avg_region_count"] == 3.5
    assert enabled["error_type_hits"] == {"network": 1}
    assert timeout_mode["warning_hits"] == {"LAYOUT_TIMEOUT_REVIEW": 1}
