import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.core.config import settings


@dataclass(frozen=True)
class RuntimeRouterDecision:
    cognitive_model: str
    stream: bool
    force_degrade_to_chat: bool
    reason: str


class RuntimeRouterController:
    """
    Runtime policy controller for Phase B:
    - Automatic degradation based on failure-rate threshold
    - Token anomaly guard based on rolling token estimate median
    """

    def __init__(self) -> None:
        self._samples: List[dict] = []
        self._max_samples: int = 400

    def record_event(
        self,
        *,
        model: str,
        success: bool,
        token_estimate: Optional[int],
        fallback_used: bool,
        reason: Optional[str] = None,
    ) -> None:
        self._samples.append(
            {
                "model": model,
                "success": bool(success),
                "token_estimate": int(token_estimate) if token_estimate is not None else None,
                "fallback_used": bool(fallback_used),
                "reason": reason or "",
            }
        )
        if len(self._samples) > self._max_samples:
            self._samples = self._samples[-self._max_samples :]

    def snapshot(self) -> Dict[str, float | int]:
        total = len(self._samples)
        if total == 0:
            return {
                "sample_count": 0,
                "failure_rate": 0.0,
                "fallback_rate": 0.0,
                "token_median": 0.0,
                "token_p95": 0.0,
            }

        failures = sum(1 for x in self._samples if not x["success"])
        fallbacks = sum(1 for x in self._samples if x["fallback_used"])
        token_values = [x["token_estimate"] for x in self._samples if isinstance(x["token_estimate"], int)]
        token_values_sorted = sorted(token_values)
        token_median = float(statistics.median(token_values_sorted)) if token_values_sorted else 0.0
        token_p95 = float(token_values_sorted[int((len(token_values_sorted) - 1) * 0.95)]) if token_values_sorted else 0.0
        return {
            "sample_count": total,
            "failure_rate": failures / total,
            "fallback_rate": fallbacks / total,
            "token_median": token_median,
            "token_p95": token_p95,
        }

    def decide_cognitive_route(
        self,
        *,
        readability_status: str,
        incoming_token_estimate: Optional[int],
        requested_model: str,
    ) -> RuntimeRouterDecision:
        if not settings.auto_circuit_controller_enabled:
            return RuntimeRouterDecision(
                cognitive_model=requested_model,
                stream=settings.deepseek_use_stream,
                force_degrade_to_chat=False,
                reason="controller_disabled",
            )

        if readability_status == "HEAVILY_ALTERED":
            return RuntimeRouterDecision(
                cognitive_model="deepseek-chat",
                stream=False,
                force_degrade_to_chat=True,
                reason="readability_heavily_altered",
            )

        snapshot = self.snapshot()
        sample_count = int(snapshot["sample_count"])
        failure_rate = float(snapshot["failure_rate"])
        token_median = float(snapshot["token_median"])

        if sample_count >= settings.auto_circuit_min_samples and failure_rate >= settings.auto_circuit_failure_rate_threshold:
            return RuntimeRouterDecision(
                cognitive_model="deepseek-chat",
                stream=False,
                force_degrade_to_chat=True,
                reason="failure_rate_threshold",
            )

        if (
            incoming_token_estimate is not None
            and token_median > 0
            and float(incoming_token_estimate) >= token_median * settings.auto_circuit_token_spike_threshold
        ):
            return RuntimeRouterDecision(
                cognitive_model="deepseek-chat",
                stream=False,
                force_degrade_to_chat=True,
                reason="token_spike_threshold",
            )

        if (
            incoming_token_estimate is not None
            and incoming_token_estimate > settings.router_budget_token_limit
        ):
            return RuntimeRouterDecision(
                cognitive_model="deepseek-chat",
                stream=False,
                force_degrade_to_chat=True,
                reason="budget_token_limit",
            )

        return RuntimeRouterDecision(
            cognitive_model=requested_model,
            stream=settings.deepseek_use_stream,
            force_degrade_to_chat=False,
            reason="default",
        )


_RUNTIME_ROUTER = RuntimeRouterController()


def get_runtime_router_controller() -> RuntimeRouterController:
    return _RUNTIME_ROUTER
