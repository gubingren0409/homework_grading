from typing import Optional


class GradingSystemError(Exception):
    """Base class for all grading system exceptions."""
    pass


class PerceptionShortCircuitError(GradingSystemError):
    """
    Raised when the perception layer detects unreadable or low-quality data.
    Used to prevent dirty data from flowing into the cognitive layer.
    """
    def __init__(self, readability_status: str, message: Optional[str] = None):
        self.readability_status = readability_status
        self.message = message or f"Perception short-circuit triggered with status: {readability_status}"
        super().__init__(self.message)


class CognitiveRefusalError(GradingSystemError):
    """Raised when the cognitive agent refuses to provide an evaluation (e.g., safety filters)."""
    pass
