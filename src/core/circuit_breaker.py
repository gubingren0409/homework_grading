"""
Phase 33: Circuit Breaker Pattern for External API Resilience

Prevents cascading failures when downstream services (Qwen/DeepSeek APIs) degrade.

Problem:
- Large model API outage → Worker retries exhaust → Tasks flood DLQ
- RPM limit reached → 100% failure rate → Wasted retry attempts
- Network issues → Slow timeouts → Worker thread starvation

Solution:
- Circuit Breaker: Detect consecutive failures → Open circuit → Fast-fail
- Exponential backoff: Gradually retry after cooling period
- Failure isolation: Separate breakers per API (Qwen/DeepSeek)

Circuit States:
1. CLOSED: Normal operation, requests pass through
2. OPEN: Consecutive failures exceed threshold, reject immediately
3. HALF_OPEN: Test recovery after timeout, allow single probe request

Benefits:
✅ Prevents Worker thread starvation (fast-fail vs timeout)
✅ Reduces wasted retry attempts (no retries during known outage)
✅ Self-healing: Automatic recovery detection
✅ Graceful degradation: Clear error messages vs silent failures
"""
import time
import logging
from typing import Callable, Any, Optional
from enum import Enum
from functools import wraps


logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"          # Normal operation
    OPEN = "open"              # Failure threshold exceeded, reject requests
    HALF_OPEN = "half_open"    # Testing recovery


class CircuitBreakerOpenError(Exception):
    """
    Raised when circuit breaker is OPEN.
    
    Indicates downstream service is degraded, request rejected to prevent
    cascading failures.
    """
    pass


class CircuitBreaker:
    """
    Circuit Breaker for external API calls.
    
    Usage:
        breaker = CircuitBreaker(
            name="qwen_api",
            failure_threshold=3,
            recovery_timeout=60,
            expected_exceptions=(openai.APIError,)
        )
        
        @breaker
        def call_qwen_api():
            return qwen_client.chat.completions.create(...)
    
    Configuration:
        failure_threshold: Consecutive failures before opening circuit
        recovery_timeout: Seconds before attempting recovery (HALF_OPEN)
        expected_exceptions: Exceptions to count as failures
    
    States:
        CLOSED: All requests pass through, failures counted
        OPEN: All requests rejected immediately (CircuitBreakerOpenError)
        HALF_OPEN: Single probe request allowed, others rejected
    
    Metrics:
        - failure_count: Consecutive failures in CLOSED state
        - success_count: Successes in HALF_OPEN state
        - last_failure_time: Timestamp of last failure
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
        expected_exceptions: tuple = (Exception,),
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Circuit breaker identifier (e.g., "qwen_api")
            failure_threshold: Consecutive failures before OPEN
            recovery_timeout: Seconds in OPEN before HALF_OPEN
            success_threshold: Successes in HALF_OPEN before CLOSED
            expected_exceptions: Exceptions counted as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.expected_exceptions = expected_exceptions
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.last_state_change: float = time.time()
        
        logger.info(
            f"[CircuitBreaker] Initialized {name}: "
            f"failure_threshold={failure_threshold}, "
            f"recovery_timeout={recovery_timeout}s"
        )
    
    def __call__(self, func: Callable) -> Callable:
        """
        Decorator: Wrap function with circuit breaker protection.
        
        Example:
            @circuit_breaker
            def risky_api_call():
                return external_api.call()
        """
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Check circuit state before execution
            if self.state == CircuitState.OPEN:
                # Circuit OPEN: Check if recovery timeout elapsed
                if self._should_attempt_reset():
                    self._transition_to_half_open()
                else:
                    # Still in cooling period, reject immediately
                    elapsed = time.time() - self.last_failure_time
                    remaining = self.recovery_timeout - elapsed
                    
                    logger.warning(
                        f"[CircuitBreaker] {self.name} OPEN: "
                        f"Rejecting request (recovery in {remaining:.1f}s)"
                    )
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker {self.name} is OPEN. "
                        f"Service degraded, retry in {remaining:.1f}s."
                    )
            
            elif self.state == CircuitState.HALF_OPEN:
                # HALF_OPEN: Only allow probe request
                if self.success_count > 0:
                    # Probe already in progress, reject others
                    logger.warning(
                        f"[CircuitBreaker] {self.name} HALF_OPEN: "
                        f"Probe in progress, rejecting concurrent request"
                    )
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker {self.name} is HALF_OPEN. "
                        f"Recovery probe in progress."
                    )
            
            # Execute protected function
            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
            
            except self.expected_exceptions as e:
                self._on_failure()
                raise  # Re-raise original exception
        
        return wrapper
    
    def _should_attempt_reset(self) -> bool:
        """Check if recovery timeout has elapsed."""
        if self.last_failure_time is None:
            return False
        
        elapsed = time.time() - self.last_failure_time
        return elapsed >= self.recovery_timeout
    
    def _transition_to_half_open(self) -> None:
        """Transition from OPEN to HALF_OPEN state."""
        self.state = CircuitState.HALF_OPEN
        self.success_count = 0
        self.last_state_change = time.time()
        
        logger.info(
            f"[CircuitBreaker] {self.name} → HALF_OPEN: "
            f"Attempting recovery probe"
        )
    
    def _on_success(self) -> None:
        """Handle successful request."""
        if self.state == CircuitState.HALF_OPEN:
            # Success in HALF_OPEN: Count towards recovery
            self.success_count += 1
            
            logger.info(
                f"[CircuitBreaker] {self.name} HALF_OPEN success: "
                f"{self.success_count}/{self.success_threshold}"
            )
            
            # Enough successes → Close circuit
            if self.success_count >= self.success_threshold:
                self._transition_to_closed()
        
        elif self.state == CircuitState.CLOSED:
            # Success in CLOSED: Reset failure counter
            if self.failure_count > 0:
                logger.info(
                    f"[CircuitBreaker] {self.name} recovered: "
                    f"Resetting failure count ({self.failure_count} → 0)"
                )
                self.failure_count = 0
    
    def _on_failure(self) -> None:
        """Handle failed request."""
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.CLOSED:
            # Failure in CLOSED: Increment counter
            self.failure_count += 1
            
            logger.warning(
                f"[CircuitBreaker] {self.name} failure: "
                f"{self.failure_count}/{self.failure_threshold}"
            )
            
            # Threshold exceeded → Open circuit
            if self.failure_count >= self.failure_threshold:
                self._transition_to_open()
        
        elif self.state == CircuitState.HALF_OPEN:
            # Failure in HALF_OPEN: Recovery failed, reopen circuit
            logger.error(
                f"[CircuitBreaker] {self.name} HALF_OPEN probe failed: "
                f"Reopening circuit"
            )
            self._transition_to_open()
    
    def _transition_to_open(self) -> None:
        """Transition to OPEN state (circuit tripped)."""
        self.state = CircuitState.OPEN
        self.last_state_change = time.time()
        
        logger.error(
            f"[CircuitBreaker] {self.name} → OPEN: "
            f"Failure threshold exceeded ({self.failure_threshold}). "
            f"Rejecting requests for {self.recovery_timeout}s."
        )
    
    def _transition_to_closed(self) -> None:
        """Transition to CLOSED state (circuit recovered)."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_state_change = time.time()
        
        logger.info(
            f"[CircuitBreaker] {self.name} → CLOSED: "
            f"Service recovered, resuming normal operation"
        )
    
    def get_state(self) -> dict:
        """
        Get current circuit breaker state for monitoring.
        
        Returns:
            Dict with state, counters, and timestamps
        """
        uptime = time.time() - self.last_state_change
        
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_failure_time": self.last_failure_time,
            "state_uptime_seconds": uptime,
        }
    
    def reset(self) -> None:
        """
        Manually reset circuit breaker to CLOSED state.
        
        Use for administrative recovery or testing.
        """
        logger.warning(f"[CircuitBreaker] {self.name} manually reset to CLOSED")
        self._transition_to_closed()
