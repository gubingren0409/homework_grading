from pathlib import Path

from scripts.configure_teacher_trial import (
    collect_teacher_trial_config_issues,
    ensure_env_file,
    is_placeholder_secret,
    parse_env_values,
    resolve_runtime_profile_updates,
    upsert_env_values,
)


def test_is_placeholder_secret_detects_template_values():
    assert is_placeholder_secret("")
    assert is_placeholder_secret("sk-your-qwen-key-1")
    assert is_placeholder_secret("change-me")
    assert not is_placeholder_secret("sk-real-key-123")


def test_upsert_env_values_replaces_and_appends(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("QWEN_API_KEYS=old\nLLM_EGRESS_ENABLED=false\n", encoding="utf-8")

    upsert_env_values(
        env_path,
        {
            "QWEN_API_KEYS": "new-qwen",
            "DEEPSEEK_API_KEYS": "new-deepseek",
            "LLM_EGRESS_ENABLED": "true",
        },
    )

    values = parse_env_values(env_path)
    assert values["QWEN_API_KEYS"] == "new-qwen"
    assert values["DEEPSEEK_API_KEYS"] == "new-deepseek"
    assert values["LLM_EGRESS_ENABLED"] == "true"


def test_collect_teacher_trial_config_issues_reports_placeholders(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "QWEN_API_KEYS=sk-your-qwen-key-1\n"
        "DEEPSEEK_API_KEYS=sk-real-key\n"
        "LLM_EGRESS_ENABLED=false\n",
        encoding="utf-8",
    )

    issues = collect_teacher_trial_config_issues(env_path)

    assert "QWEN_API_KEYS 未填写真实可用的 key。" in issues
    assert "LLM_EGRESS_ENABLED 当前不是 true，模型外呼会被阻断。" in issues


def test_resolve_runtime_profile_updates_returns_fast_profile():
    updates = resolve_runtime_profile_updates("fast")

    assert updates == {
        "QWEN_ANSWER_REGION_STRATEGY": "auto",
        "QWEN_BATCH_MAX_IMAGES": "2",
        "QWEN_SINGLE_IMAGE_CONCURRENCY": "2",
        "QWEN_ANSWER_REGION_BATCH_CONCURRENCY": "4",
        "PAPER_LAYOUT_ENABLED": "true",
    }


def test_resolve_runtime_profile_updates_returns_empty_for_none():
    assert resolve_runtime_profile_updates(None) == {}


def test_ensure_env_file_copies_example_once(tmp_path):
    env_path = tmp_path / ".env"
    env_example = tmp_path / ".env.example"
    env_example.write_text("QWEN_API_KEYS=demo\n", encoding="utf-8")

    created = ensure_env_file(env_path, env_example)
    created_again = ensure_env_file(env_path, env_example)

    assert created is True
    assert created_again is False
    assert env_path.read_text(encoding="utf-8") == "QWEN_API_KEYS=demo\n"
