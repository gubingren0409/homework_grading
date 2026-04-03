from src.core.runtime_router import RuntimeRouterController


def test_router_degrades_on_failure_rate_threshold(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "auto_circuit_controller_enabled", True)
    monkeypatch.setattr(settings, "auto_circuit_min_samples", 3)
    monkeypatch.setattr(settings, "auto_circuit_failure_rate_threshold", 0.5)
    router = RuntimeRouterController()
    router.record_event(model="deepseek-reasoner", success=False, token_estimate=100, fallback_used=False, reason="x")
    router.record_event(model="deepseek-reasoner", success=False, token_estimate=100, fallback_used=False, reason="x")
    router.record_event(model="deepseek-reasoner", success=True, token_estimate=100, fallback_used=False, reason="x")
    decision = router.decide_cognitive_route(
        readability_status="CLEAR",
        incoming_token_estimate=100,
        requested_model="deepseek-reasoner",
    )
    assert decision.force_degrade_to_chat is True
    assert decision.cognitive_model == "deepseek-chat"


def test_router_degrades_on_token_spike(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "auto_circuit_controller_enabled", True)
    monkeypatch.setattr(settings, "auto_circuit_min_samples", 2)
    monkeypatch.setattr(settings, "auto_circuit_token_spike_threshold", 1.5)
    monkeypatch.setattr(settings, "router_budget_token_limit", 10000)
    router = RuntimeRouterController()
    router.record_event(model="deepseek-reasoner", success=True, token_estimate=100, fallback_used=False, reason="x")
    router.record_event(model="deepseek-reasoner", success=True, token_estimate=100, fallback_used=False, reason="x")
    decision = router.decide_cognitive_route(
        readability_status="CLEAR",
        incoming_token_estimate=200,
        requested_model="deepseek-reasoner",
    )
    assert decision.force_degrade_to_chat is True
    assert decision.reason == "token_spike_threshold"


def test_router_follows_default_when_healthy(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "auto_circuit_controller_enabled", True)
    monkeypatch.setattr(settings, "auto_circuit_min_samples", 20)
    router = RuntimeRouterController()
    decision = router.decide_cognitive_route(
        readability_status="CLEAR",
        incoming_token_estimate=100,
        requested_model="deepseek-reasoner",
    )
    assert decision.force_degrade_to_chat is False
    assert decision.cognitive_model == "deepseek-reasoner"
