"""
Phase 33: Circuit Breaker Pattern Tests

Tests for API service-level circuit breaker protection.
Validates that downstream failures are properly isolated and recovered.
"""
import pytest
import asyncio
import fakeredis.aioredis as fakeredis_aioredis

from src.core.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenError,
)


@pytest.fixture
def breaker():
    """Create circuit breaker with short timeouts for testing."""
    redis_client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    return CircuitBreaker(
        name="test_api",
        failure_threshold=3,
        recovery_timeout=2.0,
        success_threshold=2,
        expected_exceptions=(ValueError, RuntimeError),
        redis_client=redis_client,
    )


def test_initial_state_closed(breaker):
    """Test: Circuit breaker starts in CLOSED state."""
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_success_in_closed_state(breaker):
    """Test: Successful calls in CLOSED state."""
    @breaker
    async def successful_call():
        return "success"
    
    result = await successful_call()
    assert result == "success"
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_failure_increments_counter(breaker):
    """Test: Failures increment failure counter."""
    @breaker
    async def failing_call():
        raise ValueError("API error")
    
    # First failure
    with pytest.raises(ValueError):
        await failing_call()
    assert breaker.failure_count == 1
    assert breaker.state == CircuitState.CLOSED
    
    # Second failure
    with pytest.raises(ValueError):
        await failing_call()
    assert breaker.failure_count == 2
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold(breaker):
    """Test: Circuit opens after failure threshold exceeded."""
    @breaker
    async def failing_call():
        raise ValueError("API error")
    
    # Trigger 3 failures (threshold)
    for _ in range(3):
        with pytest.raises(ValueError):
            await failing_call()
    
    # Circuit should now be OPEN
    assert breaker.state == CircuitState.OPEN
    assert breaker.failure_count == 3


@pytest.mark.asyncio
async def test_circuit_open_rejects_immediately(breaker):
    """Test: OPEN circuit rejects requests without calling function."""
    call_count = 0
    
    @breaker
    async def monitored_call():
        nonlocal call_count
        call_count += 1
        raise ValueError("API error")
    
    # Trip circuit
    for _ in range(3):
        with pytest.raises(ValueError):
            await monitored_call()
    
    assert breaker.state == CircuitState.OPEN
    initial_call_count = call_count
    
    # Next call should be rejected immediately
    with pytest.raises(CircuitBreakerOpenError, match="Circuit breaker.*is OPEN"):
        await monitored_call()
    
    # Function should NOT have been called
    assert call_count == initial_call_count


@pytest.mark.asyncio
async def test_circuit_transitions_to_half_open(breaker):
    """Test: Circuit transitions to HALF_OPEN after recovery timeout."""
    @breaker
    async def failing_call():
        raise ValueError("API error")
    
    # Trip circuit
    for _ in range(3):
        with pytest.raises(ValueError):
            await failing_call()
    
    assert breaker.state == CircuitState.OPEN
    
    # Wait for recovery timeout
    await asyncio.sleep(2.5)
    
    # Next call should transition to HALF_OPEN (but still fail)
    with pytest.raises(ValueError):
        await failing_call()
    
    # Should transition back to OPEN after probe failure
    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_recovers_after_successes(breaker):
    """Test: Circuit closes after successful probes in HALF_OPEN."""
    attempt_count = 0
    
    @breaker
    async def flaky_call():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count <= 3:
            raise ValueError("Still failing")
        return "recovered"
    
    # Trip circuit (3 failures)
    for _ in range(3):
        with pytest.raises(ValueError):
            await flaky_call()
    
    assert breaker.state == CircuitState.OPEN
    
    # Wait for recovery timeout
    await asyncio.sleep(2.5)
    
    # First probe in HALF_OPEN: success
    result = await flaky_call()
    assert result == "recovered"
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.success_count == 1
    
    # Second probe: success → Circuit closes
    result = await flaky_call()
    assert result == "recovered"
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_half_open_rejects_concurrent_requests(breaker):
    """Test: HALF_OPEN state only allows one probe request."""
    @breaker
    async def slow_recovery():
        await asyncio.sleep(0.5)
        return "success"
    
    # Trip circuit
    @breaker
    async def failing_call():
        raise ValueError("API error")
    
    for _ in range(3):
        with pytest.raises(ValueError):
            await failing_call()
    
    # Wait for recovery
    await asyncio.sleep(2.5)
    
    # Start first probe (will be slow)
    task1 = asyncio.create_task(slow_recovery())
    
    # Wait for probe to start
    await asyncio.sleep(0.1)
    
    # Second request should be rejected
    with pytest.raises(CircuitBreakerOpenError, match="HALF_OPEN.*Probe in progress"):
        await slow_recovery()
    
    # First probe should complete
    result = await task1
    assert result == "success"


@pytest.mark.asyncio
async def test_unexpected_exception_not_counted(breaker):
    """Test: Exceptions not in expected_exceptions don't trip circuit."""
    @breaker
    async def call_with_unexpected_error():
        raise TypeError("Unexpected error")
    
    # Raise unexpected exception multiple times
    for _ in range(5):
        with pytest.raises(TypeError):
            await call_with_unexpected_error()
    
    # Circuit should still be CLOSED
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_get_state_returns_metrics(breaker):
    """Test: get_state() returns circuit breaker metrics."""
    state = breaker.snapshot()
    
    assert state["name"] == "test_api"
    assert state["state"] == "closed"
    assert state["failure_count"] == 0
    assert state["failure_threshold"] == 3
    assert state["recovery_timeout"] == 2.0
    assert "state_uptime_seconds" in state


@pytest.mark.asyncio
async def test_manual_reset(breaker):
    """Test: Manual reset transitions circuit to CLOSED."""
    @breaker
    async def failing_call():
        raise ValueError("API error")
    
    # Trip circuit
    for _ in range(3):
        with pytest.raises(ValueError):
            await failing_call()
    
    assert breaker.state == CircuitState.OPEN
    
    # Manual reset
    await breaker.reset()
    
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_success_resets_failure_count_in_closed(breaker):
    """Test: Success in CLOSED state resets failure counter."""
    @breaker
    async def flaky_call(should_fail):
        if should_fail:
            raise ValueError("API error")
        return "success"
    
    # Two failures
    for _ in range(2):
        with pytest.raises(ValueError):
            await flaky_call(True)
    
    assert breaker.failure_count == 2
    assert breaker.state == CircuitState.CLOSED
    
    # One success → Reset counter
    result = await flaky_call(False)
    assert result == "success"
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_circuit_breaker_with_real_api_pattern(breaker):
    """
    Integration test: Simulate real API failure and recovery pattern.
    """
    api_healthy = False
    call_count = 0
    
    @breaker
    async def api_call():
        nonlocal call_count
        call_count += 1
        
        if not api_healthy:
            raise ValueError("Service unavailable")
        return {"status": "ok"}
    
    # Phase 1: API is down, circuit trips
    for _ in range(3):
        with pytest.raises(ValueError):
            await api_call()
    
    assert breaker.state == CircuitState.OPEN
    assert call_count == 3
    
    # Phase 2: Requests rejected during cooling period
    with pytest.raises(CircuitBreakerOpenError):
        await api_call()
    
    assert call_count == 3  # Function not called
    
    # Phase 3: Wait for recovery timeout
    await asyncio.sleep(2.5)
    
    # Phase 4: API still down, probe fails
    with pytest.raises(ValueError):
        await api_call()
    
    assert breaker.state == CircuitState.OPEN  # Reopened
    assert call_count == 4
    
    # Phase 5: Wait again, API recovers
    await asyncio.sleep(2.5)
    api_healthy = True
    
    # Phase 6: Probe succeeds
    result = await api_call()
    assert result == {"status": "ok"}
    assert breaker.state == CircuitState.HALF_OPEN
    
    # Phase 7: Second success closes circuit
    result = await api_call()
    assert result == {"status": "ok"}
    assert breaker.state == CircuitState.CLOSED
