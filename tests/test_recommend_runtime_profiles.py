from scripts.recommend_runtime_profiles import build_runtime_profile_recommendations, render_markdown


def test_build_runtime_profile_recommendations_returns_named_profiles():
    recommendations = build_runtime_profile_recommendations()

    assert set(recommendations["profiles"]) == {"dev_smoke", "teacher_trial", "full_regression"}
    assert recommendations["profiles"]["dev_smoke"]["source_profile"] == "dev-smoke"
    assert recommendations["profiles"]["teacher_trial"]["env_updates"]["QWEN_SINGLE_IMAGE_CONCURRENCY"] == "1"


def test_render_markdown_outputs_profile_table():
    markdown = render_markdown(build_runtime_profile_recommendations())

    assert "# Runtime profile recommendations" in markdown
    assert "| dev_smoke | dev-smoke | 开发期快速冒烟和长尾复现 |" in markdown
    assert "| teacher_trial | teacher-trial | 教师本地试用与最小真实整卷运行 |" in markdown
