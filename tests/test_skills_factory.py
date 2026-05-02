from src.core.config import settings
from src.skills.factory import build_skill_bundle
from src.skills.layout_http import HttpLayoutParserSkill
from src.skills.layout_mineru import MinerULayoutSkill
from src.skills.validation_http import HttpValidationExecutionSkill


def test_skill_bundle_defaults_disabled(monkeypatch):
    monkeypatch.setattr(settings, "skill_layout_parser_enabled", False)
    monkeypatch.setattr(settings, "skill_validation_enabled", False)
    bundle = build_skill_bundle(db_path="outputs/test.db")
    assert bundle.layout_parser is None
    assert bundle.validation_executor is None


def test_skill_bundle_enables_layout_http(monkeypatch):
    monkeypatch.setattr(settings, "skill_layout_parser_enabled", True)
    monkeypatch.setattr(settings, "skill_layout_parser_provider", "llamaparse")
    monkeypatch.setattr(settings, "skill_layout_parser_api_url", "http://localhost:18080/layout")
    monkeypatch.setattr(settings, "skill_layout_parser_api_key", "k")
    monkeypatch.setattr(settings, "skill_layout_parser_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "skill_validation_enabled", False)
    bundle = build_skill_bundle(db_path="outputs/test.db")
    assert isinstance(bundle.layout_parser, HttpLayoutParserSkill)


def test_skill_bundle_enables_layout_mineru(monkeypatch):
    monkeypatch.setattr(settings, "skill_layout_parser_enabled", True)
    monkeypatch.setattr(settings, "skill_layout_parser_provider", "mineru")
    monkeypatch.setattr(settings, "skill_layout_parser_api_url", "http://localhost:18082")
    monkeypatch.setattr(settings, "skill_layout_parser_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "skill_validation_enabled", False)
    bundle = build_skill_bundle(db_path="outputs/test.db")
    assert isinstance(bundle.layout_parser, MinerULayoutSkill)


def test_skill_bundle_enables_validation_http(monkeypatch):
    monkeypatch.setattr(settings, "skill_layout_parser_enabled", False)
    monkeypatch.setattr(settings, "skill_validation_enabled", True)
    monkeypatch.setattr(settings, "skill_validation_provider", "e2b")
    monkeypatch.setattr(settings, "skill_validation_api_url", "http://localhost:18081/validate")
    monkeypatch.setattr(settings, "skill_validation_api_key", "k")
    monkeypatch.setattr(settings, "skill_validation_timeout_seconds", 5.0)
    bundle = build_skill_bundle(db_path="outputs/test.db")
    assert isinstance(bundle.validation_executor, HttpValidationExecutionSkill)
