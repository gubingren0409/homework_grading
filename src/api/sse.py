"""
Phase 33: Server-Sent Events (SSE) - Distributed Event-Driven Architecture.

Solves multi-node state synchronization via Redis Pub/Sub:
1. Worker updates DB + PUBLISH event to Redis channel
2. API node SUBSCRIBE to channel + forward to SSE client
3. Database polling as fallback (message loss tolerance)

Architecture:
1. Client: EventSource(/api/v1/tasks/{task_id}/stream)
2. Server: Long-lived HTTP connection with text/event-stream
3. Backend: Redis Pub/Sub (primary) + DB polling (fallback)
4. Termination: Connection closed when task reaches terminal state

Critical Fix:
- ❌ Phase 32: Database polling only (1s latency, multi-node blind)
- ✅ Phase 33: Redis Pub/Sub (sub-100ms latency, multi-node aware)

Benefits:
- ✅ True event-driven (Worker → Redis → API node)
- ✅ Multi-node deployment support (distributed state notification)
- ✅ Sub-100ms latency (vs 1s database polling)
- ✅ Fallback resilience (DB polling if Pub/Sub message lost)
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from sse_starlette.sse import EventSourceResponse
import redis.asyncio as aioredis

from src.core.config import settings
from src.db.client import get_task


logger = logging.getLogger(__name__)


# Redis Pub/Sub channel naming convention
def get_task_channel(task_id: str) -> str:
    """Get Redis Pub/Sub channel name for task status updates."""
    return f"task_status:{task_id}"


async def task_status_stream(
    db_path: str,
    task_id: str,
    timeout: int = 300,
    fallback_poll_interval: float = 5.0,
) -> AsyncGenerator[dict, None]:
    """
    Phase 33: SSE generator with Redis Pub/Sub + fallback polling.
    
    Strategy:
    1. Subscribe to Redis channel task_status:{task_id}
    2. Listen for Worker-published status updates (primary path)
    3. Fallback to database polling every 5s (tolerance for message loss)
    4. Terminate on terminal state or timeout
    
    Args:
        db_path: SQLite database path
        task_id: Business task UUID
        timeout: Maximum connection lifetime (seconds)
        fallback_poll_interval: Database polling interval for fallback (seconds)
    
    Yields:
        SSE events with task status updates
        
    Termination Conditions:
        - Task reaches terminal state (COMPLETED, FAILED)
        - Timeout exceeded
        - Client disconnects
    
    Example SSE event:
        {
            "event": "status_update",
            "data": {
                "task_id": "uuid",
                "status": "PROCESSING",
                "progress": 0.5,
                "source": "pubsub"  # or "fallback"
            }
        }
    """
    start_time = asyncio.get_event_loop().time()
    last_status = None
    redis_client = None
    pubsub = None
    
    try:
        # Step 1: Connect to Redis Pub/Sub
        redis_client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        pubsub = redis_client.pubsub()
        channel = get_task_channel(task_id)
        await pubsub.subscribe(channel)
        
        logger.info(f"[SSE] Task {task_id} subscribed to Redis channel: {channel}")
        
        # Step 2: Send initial status immediately from DB
        task = await get_task(db_path, task_id)
        if not task:
            logger.error(f"[SSE] Task {task_id} not found in database")
            yield {
                "event": "error",
                "data": json.dumps({"error": "Task not found"})
            }
            return
        
        # Emit initial status
        initial_event = await _build_status_event(task, source="initial")
        yield {
            "event": "status_update",
            "data": json.dumps(initial_event)
        }
        last_status = task["status"]
        
        # Check if already terminal
        if task["status"] in ["COMPLETED", "FAILED"]:
            logger.info(f"[SSE] Task {task_id} already in terminal state")
            yield {
                "event": "complete",
                "data": json.dumps({"task_id": task_id, "status": task["status"]})
            }
            return
        
        # Step 3: Hybrid listen loop (Pub/Sub + fallback polling)
        last_poll_time = asyncio.get_event_loop().time()
        
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
            
            # Try to receive Redis Pub/Sub message (non-blocking with timeout)
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=1.0  # 1s timeout to allow fallback checks
                )
                
                if message and message["type"] == "message":
                    # Received Worker-published status update (PRIMARY PATH)
                    event_data = json.loads(message["data"])
                    event_data["source"] = "pubsub"
                    
                    current_status = event_data.get("status")
                    
                    # Only emit if status changed
                    if current_status and current_status != last_status:
                        logger.info(f"[SSE] Task {task_id} status update via Pub/Sub: {current_status}")
                        yield {
                            "event": "status_update",
                            "data": json.dumps(event_data)
                        }
                        last_status = current_status
                        last_poll_time = asyncio.get_event_loop().time()  # Reset poll timer
                        
                        # Terminate on terminal state
                        if current_status in ["COMPLETED", "FAILED"]:
                            logger.info(f"[SSE] Task {task_id} reached terminal state via Pub/Sub")
                            yield {
                                "event": "complete",
                                "data": json.dumps({"task_id": task_id, "status": current_status})
                            }
                            break
            
            except asyncio.TimeoutError:
                # No Pub/Sub message received - check fallback polling
                pass
            
            # Fallback: Database polling (if no Pub/Sub update for fallback_poll_interval)
            elapsed_since_poll = asyncio.get_event_loop().time() - last_poll_time
            if elapsed_since_poll >= fallback_poll_interval:
                task = await get_task(db_path, task_id)
                
                if not task:
                    logger.error(f"[SSE] Task {task_id} disappeared from database")
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": "Task not found"})
                    }
                    break
                
                current_status = task["status"]
                
                # Only emit if status changed (FALLBACK PATH)
                if current_status != last_status:
                    logger.warning(f"[SSE] Task {task_id} status update via DB fallback: {current_status}")
                    event_data = await _build_status_event(task, source="fallback")
                    yield {
                        "event": "status_update",
                        "data": json.dumps(event_data)
                    }
                    last_status = current_status
                    
                    # Terminate on terminal state
                    if current_status in ["COMPLETED", "FAILED"]:
                        logger.info(f"[SSE] Task {task_id} reached terminal state via DB fallback")
                        yield {
                            "event": "complete",
                            "data": json.dumps({"task_id": task_id, "status": current_status})
                        }
                        break
                
                last_poll_time = asyncio.get_event_loop().time()
    
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
    
    finally:
        # Cleanup: Unsubscribe and close Redis connection
        if pubsub:
            await pubsub.unsubscribe(get_task_channel(task_id))
            await pubsub.aclose()
        if redis_client:
            await redis_client.aclose()
        logger.info(f"[SSE] Cleaned up Redis connection for task {task_id}")


async def _build_status_event(task: dict, source: str) -> dict:
    """
    Build SSE event data from task record.
    
    Args:
        task: Task record from database
        source: Event source ("initial", "pubsub", "fallback")
    
    Returns:
        Event data dict for JSON serialization
    """
    event_data = {
        "task_id": task["task_id"],
        "status": task["status"],
        "source": source,
    }
    
    # Enrich with status-specific data
    if task["status"] in ["PENDING", "PROCESSING"]:
        event_data["progress"] = 0.5 if task["status"] == "PROCESSING" else 0.0
        event_data["eta_seconds"] = 45
    
    elif task["status"] == "COMPLETED":
        event_data["message"] = "Grading completed successfully"
    
    elif task["status"] == "FAILED":
        raw_error = task.get("error_message", "Unknown error")
        event_data["error"] = raw_error[:200]  # Truncate
    
    return event_data


def create_sse_response(db_path: str, task_id: str) -> EventSourceResponse:
    """
    Phase 33: Factory for SSE response with Redis Pub/Sub backend.
    
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
            console.log('Status:', data.status, 'Progress:', data.progress, 'Source:', data.source);
            
            if (data.source === 'pubsub') {
                // Real-time update from Worker (sub-100ms latency)
            } else if (data.source === 'fallback') {
                // Fallback DB polling (5s latency)
            }
        });
        
        eventSource.addEventListener('complete', (event) => {
            eventSource.close();
            // Fetch full results via GET /tasks/{task_id}
        });
    
    Multi-node Behavior:
        - ✅ Worker updates DB on Node A
        - ✅ Worker PUBLISH to Redis (visible to all nodes)
        - ✅ API Node B (holding SSE connection) receives event
        - ✅ API Node B forwards to client (sub-100ms latency)
    """
    return EventSourceResponse(
        task_status_stream(db_path, task_id),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


# Utility function for Worker to publish status updates
async def publish_task_status(task_id: str, status: str, **kwargs) -> None:
    """
    Phase 33: Publish task status update to Redis Pub/Sub.
    
    Called by Worker after updating database to notify all API nodes.
    
    Args:
        task_id: Business task UUID
        status: Task status (PENDING, PROCESSING, COMPLETED, FAILED)
        **kwargs: Additional event data (progress, error, etc.)
    
    Example:
        await publish_task_status(task_id, "COMPLETED", message="Grading finished")
    """
    redis_client = None
    try:
        redis_client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        
        channel = get_task_channel(task_id)
        event_data = {
            "task_id": task_id,
            "status": status,
            **kwargs,
        }
        
        await redis_client.publish(channel, json.dumps(event_data))
        logger.info(f"[PubSub] Published status update for task {task_id}: {status}")
    
    except Exception as e:
        # Non-blocking: If Pub/Sub fails, SSE will fallback to DB polling
        logger.error(f"[PubSub] Failed to publish task {task_id} status: {e}")
    
    finally:
        if redis_client:
            await redis_client.aclose()
