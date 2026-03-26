# Phase 29: Critical Fixes - Serialization & State Machine Hardening

**Status**: ✅ Complete (5/5 tests passed)  
**Date**: 2026-03-26  
**Commit**: TBD

---

## 🎯 Critical Defects Fixed

### 1. Payload Serialization Crash (EncodeError Prevention)

**Defect**: Celery JSON serializer crashes when encountering non-primitive types (Path objects, raw bytes, Pydantic models).

**Fix**: Enforced strict scalarization in API gateway:
```python
# Before (Phase 28)
files_data.append((list(content), file.filename))  # filename could be Path object

# After (Phase 29)
files_data.append((list(content), str(file.filename)))  # Force string conversion
```

**Validation**: `test_serialization_boundary_bytes_to_int_list` - Verifies JSON serializability before Celery dispatch.

---

### 2. State Machine Deadlock (Zombie Task Prevention)

**Defect**: Worker crashes (OOM, SIGKILL) leave tasks stuck in `PROCESSING` state forever, causing infinite client polling.

**Fix**: Implemented Zombie Task Sweeper daemon:
```bash
python scripts/zombie_sweeper.py --daemon --timeout 600 --interval 60
```

**Features**:
- Detects tasks stuck in `PROCESSING` beyond timeout threshold (default: 10 minutes)
- Marks zombies as `FAILED` with descriptive error message
- Runs as background daemon or one-shot CLI
- Supports dry-run mode for auditing

**Validation**: `test_zombie_task_detection_timeout` - Simulates worker crash and verifies automatic cleanup.

---

### 3. Worker Async Event Loop Collision

**Defect**: `asyncio.run()` fails inside pytest async tests with "event loop already running" error.

**Fix**: Implemented adaptive event loop handling with `nest-asyncio`:
```python
def run_async(coro):
    """Helper to run async code in sync Celery task context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
```

---

## 🔒 Polling Contract Strengthening

### Enhanced TaskStatusResponse Schema

```python
class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    error_code: Optional[str] = None       # NEW: Sanitized error codes
    error_message: Optional[str] = None
    progress: Optional[float] = None        # NEW: 0.0-1.0 progress indicator
    eta_seconds: Optional[int] = None       # NEW: Estimated completion time
    results: Optional[List[Dict]] = None
```

### Status-Specific Enrichment

| Status | Enrichment |
|--------|------------|
| `PENDING` | progress=0.0, eta_seconds=45 |
| `PROCESSING` | progress=0.5, eta_seconds=30 |
| `COMPLETED` | results=[...] (full payload) |
| `FAILED` | error_code=INTERNAL_ERROR (sanitized) |
| `REJECTED` | error_code=INPUT_REJECTED |

### Security: Error Message Sanitization

**Problem**: Raw Python stack traces leak internal paths and secrets.

**Solution**: Pattern detection and replacement:
```python
if "Traceback" in raw_error or "File " in raw_error:
    response_data["error_code"] = "INTERNAL_ERROR"
    response_data["error_message"] = "Internal processing error. Contact support."
```

**Validation**: `test_polling_endpoint_sanitizes_internal_errors` - Ensures no stack traces leak to clients.

---

## 🧪 Testing Strategy: Fakeredis Integration

### Why Fakeredis > Celery.apply()

| Approach | Serialization Testing | Broker Topology | Production Parity |
|----------|----------------------|-----------------|-------------------|
| `apply()` | ❌ Bypassed | ❌ Bypassed | Low |
| `fakeredis` | ✅ Full coverage | ✅ In-memory Redis | High |

### Test Coverage

1. **test_serialization_boundary_bytes_to_int_list**: Validates JSON-serializable payload construction
2. **test_zombie_task_detection_timeout**: Verifies sweeper marks stuck tasks as FAILED
3. **test_acks_late_configuration**: Confirms `task_acks_late=True` for message durability
4. **test_polling_endpoint_contract_pending_status**: Ensures progress/ETA fields present
5. **test_polling_endpoint_sanitizes_internal_errors**: Blocks stack trace leakage

**Result**: ✅ 5/5 tests passed

---

## 📦 Deliverables

| File | Change |
|------|--------|
| `requirements.txt` | Added `fakeredis`, `pytest-celery`, `nest-asyncio` |
| `src/api/routes.py` | Forced `str()` conversion, enhanced response schema |
| `src/worker/main.py` | Adaptive async loop handling with nest-asyncio |
| `scripts/zombie_sweeper.py` | NEW: Background daemon for stuck task cleanup |
| `tests/test_phase29_integration.py` | NEW: 5 integration tests with fakeredis |

---

## 🔧 Configuration Verification

### Worker Configuration (src/worker/main.py)
```python
app.conf.update(
    task_acks_late=True,              # ✅ Prevent message loss on crash
    worker_prefetch_multiplier=1,     # ✅ Prevent task hoarding
    broker_connection_retry_on_startup=True,  # ✅ Tolerate Redis delays
)
```

---

## 🚀 Deployment Checklist

### 1. Start Zombie Sweeper Daemon
```bash
nohup python scripts/zombie_sweeper.py \
  --daemon \
  --timeout 600 \
  --interval 60 \
  --db-path /app/outputs/grading_database.db \
  > /var/log/zombie_sweeper.log 2>&1 &
```

### 2. Verify Worker Configuration
```bash
celery -A src.worker.main inspect conf | grep task_acks_late
# Expected: task_acks_late: True
```

### 3. Monitor Zombie Detection
```bash
# One-shot audit
python scripts/zombie_sweeper.py --dry-run

# Check logs
tail -f /var/log/zombie_sweeper.log
```

---

## 📊 Test Results

```
======================== 5 passed, 1 warning in 1.10s =========================
✅ test_serialization_boundary_bytes_to_int_list
✅ test_zombie_task_detection_timeout
✅ test_acks_late_configuration
✅ test_polling_endpoint_contract_pending_status
✅ test_polling_endpoint_sanitizes_internal_errors
```

---

## 🔗 Related Phases

- **Phase 28**: Initial Celery decoupling (had serialization vulnerability)
- **Phase 29**: Critical hardening (this document)
- **Future**: Implement real-time progress tracking via Celery backend state
