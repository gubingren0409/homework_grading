"""
Phase 33: Celery Worker - Distributed Event-Driven Architecture

This module decouples AI computation from the FastAPI gateway.
Workers consume tasks from Redis queue and execute GradingWorkflow asynchronously.

Phase 33 Enhancements:
- Redis Pub/Sub: Worker publishes status updates after DB writes
- Multi-node support: API nodes receive events regardless of physical location
- Event-driven SSE: Sub-100ms latency vs 1s database polling

Usage:
    celery -A src.worker.main worker --loglevel=info --concurrency=4
"""
import asyncio
import logging
from typing import List, Tuple, Dict, Any

from celery import Celery

from src.core.config import settings
from src.core.exceptions import PerceptionShortCircuitError
from src.core.storage_adapter import storage
from src.db.client import update_task_status, save_grading_result
from src.orchestration.workflow import GradingWorkflow
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine


logger = logging.getLogger(__name__)

# Celery Application Initialization
app = Celery(
    "homework_grader_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# Celery Configuration (Phase 28, Phase 32: DLQ)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,  # Result TTL: 1 hour
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # Only ack after task completion (failure-tolerant)
    worker_prefetch_multiplier=1,  # Prevent task hoarding in workers
    broker_connection_retry_on_startup=True,  # Tolerate Redis startup delays
    
    # Phase 32: Dead Letter Queue (DLQ) Configuration
    task_reject_on_worker_lost=True,  # Send to DLQ if worker crashes
    task_default_max_retries=2,  # Global max retries
)

# Phase 32: Dead Letter Queue Names
DLQ_QUEUE_NAME = "grading_tasks_dlq"
DLQ_EXCHANGE = "dlq"


def _build_workflow() -> GradingWorkflow:
    """
    Factory function: Instantiate GradingWorkflow with fresh engine instances.
    Each worker process maintains independent engine pools.
    """
    perception_engine = QwenVLMPerceptionEngine()
    cognitive_agent = DeepSeekCognitiveEngine()
    return GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
    )


@app.task(bind=True, max_retries=2, default_retry_delay=10)
def grade_homework_task(
    self,
    task_id: str,
    payload: Dict[str, Any],  # Phase 32: Storage adapter payload with file_refs
    db_path: str,
) -> dict:
    """
    Celery Task: Execute grading workflow in isolated worker process.

    Args:
        task_id: Business task UUID
        payload: Storage adapter payload with {"file_refs": ["file://..." or "s3://..."]}
        db_path: SQLite database path

    Returns:
        Status dict with task_id and completion state

    Retry Policy:
        - Max 2 retries on transient failures
        - 10s delay between retries
        - Permanent failure marked as FAILED in DB
    """
    # Phase 30: Explicit event loop creation/disposal (no nest-asyncio pollution)
    def run_async(coro):
        """
        Standard async bridge for Celery sync context.
        Creates isolated event loop per invocation.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    
    try:
        # Step 1: Mark task as processing
        run_async(update_task_status(db_path, task_id, "PROCESSING"))
        # Phase 33: Publish status update to Redis Pub/Sub
        run_async(_publish_status(task_id, "PROCESSING", progress=0.0))
        logger.info(f"[Worker] Task {task_id} started processing")

        # Step 2: Retrieve files from storage backend (Phase 32)
        file_refs = payload.get("file_refs", [])
        reconstructed_files = storage.retrieve_files(file_refs)

        # Step 3: Initialize workflow (worker-local instance)
        workflow = _build_workflow()

        # Step 4: Execute core grading pipeline
        report = run_async(workflow.run_pipeline(reconstructed_files))

        # Step 5: Persist results
        student_id = reconstructed_files[0][1] if reconstructed_files else task_id
        run_async(save_grading_result(db_path, task_id, student_id, report))
        run_async(update_task_status(db_path, task_id, "COMPLETED"))
        # Phase 33: Publish completion event to Redis Pub/Sub
        run_async(_publish_status(task_id, "COMPLETED", message="Grading completed successfully"))

        # Step 6: Cleanup via storage adapter (Phase 32)
        storage.cleanup_task(task_id)

        logger.info(f"[Worker] Task {task_id} completed successfully")
        return {"status": "success", "task_id": task_id}

    except PerceptionShortCircuitError as e:
        # Defensive rejection (HEAVILY_ALTERED, UNREADABLE, blank detection)
        logger.warning(f"[Worker] Task {task_id} rejected by perception layer: {e}")
        run_async(
            update_task_status(
                db_path,
                task_id,
                "REJECTED",
                error=f"Perception short-circuit: {e.readability_status}",
            )
        )
        # Phase 33: Publish rejection event to Redis Pub/Sub
        run_async(_publish_status(task_id, "REJECTED", error=str(e)))
        # Cleanup on rejection
        storage.cleanup_task(task_id)
        return {"status": "rejected", "reason": str(e)}

    except Exception as e:
        # Transient failure: Retry logic
        logger.error(f"[Worker] Task {task_id} failed (attempt {self.request.retries + 1}): {e}")
        run_async(update_task_status(db_path, task_id, "FAILED", error=str(e)))
        # Phase 33: Publish failure event to Redis Pub/Sub
        run_async(_publish_status(task_id, "FAILED", error=str(e)))

        # Cleanup on permanent failure (after max retries)
        if self.request.retries >= self.max_retries:
            storage.cleanup_task(task_id)
            
            # Phase 32: Route to Dead Letter Queue for audit
            _route_to_dlq(task_id, payload, db_path, str(e))
            
            logger.critical(f"[Worker] Task {task_id} permanently failed and routed to DLQ")
            return {"status": "failed", "error": str(e)}

        # Retry if attempts remain
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        # Permanent failure after max retries
        logger.critical(f"[Worker] Task {task_id} permanently failed after {self.max_retries} retries")
        return {"status": "failed", "error": str(e)}


async def _publish_status(task_id: str, status: str, **kwargs) -> None:
    """
    Phase 33: Publish task status update to Redis Pub/Sub.
    
    Called after database update to notify all API nodes (multi-node support).
    Non-blocking: If Pub/Sub fails, API nodes fallback to DB polling.
    
    Args:
        task_id: Business task UUID
        status: Task status (PENDING, PROCESSING, COMPLETED, FAILED, REJECTED)
        **kwargs: Additional event data (progress, error, message, etc.)
    """
    import redis.asyncio as aioredis
    import json
    
    redis_client = None
    try:
        redis_client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        
        channel = f"task_status:{task_id}"
        event_data = {
            "task_id": task_id,
            "status": status,
            **kwargs,
        }
        
        await redis_client.publish(channel, json.dumps(event_data))
        logger.info(f"[Worker-PubSub] Published status update for task {task_id}: {status}")
    
    except Exception as e:
        # Non-critical: SSE will fallback to DB polling
        logger.warning(f"[Worker-PubSub] Failed to publish task {task_id} status: {e}")
    
    finally:
        if redis_client:
            await redis_client.close()


def _route_to_dlq(task_id: str, payload: Dict[str, Any], db_path: str, error: str) -> None:
    """
    Phase 32: Route permanently failed task to Dead Letter Queue.
    
    Poison messages (tasks that crash even after max retries) are stored
    in a separate Redis queue for manual inspection and replay.
    
    Args:
        task_id: Business task UUID
        payload: Original Celery payload
        db_path: Database path
        error: Error message from final failure
    """"""
    import redis
    import json
    
    try:
        # Connect to Redis DLQ
        redis_client = redis.from_url(settings.redis_url)
        
        # Package task metadata for audit
        dlq_entry = {
            "task_id": task_id,
            "payload": payload,
            "db_path": db_path,
            "error": error,
            "failed_at": __import__('datetime').datetime.utcnow().isoformat(),
            "retry_count": 2,  # Max retries exhausted
        }
        
        # Push to DLQ (Redis list)
        redis_client.lpush(DLQ_QUEUE_NAME, json.dumps(dlq_entry))
        
        logger.warning(
            f"[DLQ] Task {task_id} routed to dead letter queue. "
            f"Error: {error[:100]}"
        )
        
    except Exception as dlq_error:
        logger.error(f"[DLQ] Failed to route task {task_id} to DLQ: {dlq_error}")

