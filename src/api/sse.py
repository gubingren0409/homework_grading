"""
Phase 32: Server-Sent Events (SSE) - Real-time task status push.

Eliminates polling storm anti-pattern by providing unidirectional status updates.
Clients subscribe once and receive updates as they occur.

Architecture:
1. Client: EventSource(/api/v1/tasks/{task_id}/stream)
2. Server: Long-lived HTTP connection with text/event-stream
3. Backend: Redis Pub/Sub or database polling for state changes
4. Termination: Connection closed when task reaches terminal state

Benefits:
- ✅ Eliminates polling overhead (30 req/min → 1 connection)
- ✅ Sub-second latency (vs 2s polling interval)
- ✅ Standards-compliant (SSE supported by all modern browsers)
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from sse_starlette.sse import EventSourceResponse

from src.db.client import get_task


logger = logging.getLogger(__name__)


async def task_status_stream(
    db_path: str,
    task_id: str,
    poll_interval: float = 1.0,
    timeout: int = 300,
) -> AsyncGenerator[dict, None]:
    """
    SSE generator: Stream task status updates until terminal state.
    
    Args:
        db_path: SQLite database path
        task_id: Business task UUID
        poll_interval: Database polling interval (seconds)
        timeout: Maximum connection lifetime (seconds)
    
    Yields:
        SSE events with task status updates
        
    Termination Conditions:
        - Task reaches terminal state (COMPLETED, FAILED, REJECTED)
        - Timeout exceeded
        - Client disconnects
    
    Example SSE event:
        {
            "event": "status_update",
            "data": {
                "task_id": "uuid",
                "status": "PROCESSING",
                "progress": 0.5
            }
        }
    """
    start_time = asyncio.get_event_loop().time()
    last_status = None
    
    try:
        while True:
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning(f"[SSE] Task {task_id} stream timeout after {timeout}s")
                yield {
                    "event": "timeout",
                    "data": json.dumps({"message": "Stream timeout exceeded"})
                }
                break
            
            # Fetch current task state
            task = await get_task(db_path, task_id)
            
            if not task:
                logger.error(f"[SSE] Task {task_id} not found in database")
                yield {
                    "event": "error",
                    "data": json.dumps({"error": "Task not found"})
                }
                break
            
            current_status = task["status"]
            
            # Only emit event if status changed (avoid duplicate events)
            if current_status != last_status:
                event_data = {
                    "task_id": task_id,
                    "status": current_status,
                }
                
                # Enrich with status-specific data
                if current_status in ["PENDING", "PROCESSING"]:
                    event_data["progress"] = 0.5 if current_status == "PROCESSING" else 0.0
                    event_data["eta_seconds"] = 45
                
                elif current_status == "COMPLETED":
                    # Include result summary (full results via separate GET endpoint)
                    event_data["message"] = "Grading completed successfully"
                
                elif current_status in ["FAILED", "REJECTED"]:
                    # Include sanitized error message
                    raw_error = task.get("error_message", "Unknown error")
                    event_data["error"] = raw_error[:200]  # Truncate
                
                logger.info(f"[SSE] Task {task_id} status: {current_status}")
                yield {
                    "event": "status_update",
                    "data": json.dumps(event_data)
                }
                
                last_status = current_status
            
            # Terminate on terminal states
            if current_status in ["COMPLETED", "FAILED", "REJECTED"]:
                logger.info(f"[SSE] Task {task_id} reached terminal state: {current_status}")
                yield {
                    "event": "complete",
                    "data": json.dumps({"task_id": task_id, "status": current_status})
                }
                break
            
            # Wait before next poll
            await asyncio.sleep(poll_interval)
    
    except asyncio.CancelledError:
        # Client disconnected
        logger.info(f"[SSE] Client disconnected from task {task_id} stream")
        raise
    
    except Exception as e:
        logger.error(f"[SSE] Error streaming task {task_id}: {e}")
        yield {
            "event": "error",
            "data": json.dumps({"error": "Internal server error"})
        }


def create_sse_response(db_path: str, task_id: str) -> EventSourceResponse:
    """
    Factory: Create SSE response for task status streaming.
    
    Args:
        db_path: SQLite database path
        task_id: Business task UUID
    
    Returns:
        EventSourceResponse with text/event-stream content-type
    
    Usage in FastAPI:
        @router.get("/tasks/{task_id}/stream")
        async def stream_task_status(task_id: str, db_path: str = Depends(get_db_path)):
            return create_sse_response(db_path, task_id)
    
    Client-side (JavaScript):
        const eventSource = new EventSource('/api/v1/tasks/{task_id}/stream');
        
        eventSource.addEventListener('status_update', (event) => {
            const data = JSON.parse(event.data);
            console.log('Status:', data.status, 'Progress:', data.progress);
        });
        
        eventSource.addEventListener('complete', (event) => {
            eventSource.close();
            // Fetch full results via GET /tasks/{task_id}
        });
    """
    return EventSourceResponse(
        task_status_stream(db_path, task_id),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
