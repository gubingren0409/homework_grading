"""
Phase 28: Celery Worker - Physical Isolation Layer

This module decouples AI computation from the FastAPI gateway.
Workers consume tasks from Redis queue and execute GradingWorkflow asynchronously.

Usage:
    celery -A src.worker.main worker --loglevel=info --concurrency=4
"""
import asyncio
import logging
from typing import List, Tuple

from celery import Celery

from src.core.config import settings
from src.core.exceptions import PerceptionShortCircuitError
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

# Celery Configuration (Phase 28)
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
)


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
    files_data: List[Tuple[List[int], str]],  # JSON-serialized bytes as int list
    db_path: str,
) -> dict:
    """
    Celery Task: Execute grading workflow in isolated worker process.

    Args:
        task_id: Business task UUID
        files_data: List of (byte_array_as_ints, filename) tuples
        db_path: SQLite database path

    Returns:
        Status dict with task_id and completion state

    Retry Policy:
        - Max 2 retries on transient failures
        - 10s delay between retries
        - Permanent failure marked as FAILED in DB
    """
    # Phase 29: Create event loop for async operations in sync Celery task
    def run_async(coro):
        """Helper to run async code in sync Celery task context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop already running (pytest), use nest_asyncio
                import nest_asyncio
                nest_asyncio.apply()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    
    try:
        # Step 1: Mark task as processing
        run_async(update_task_status(db_path, task_id, "PROCESSING"))
        logger.info(f"[Worker] Task {task_id} started processing")

        # Step 2: Deserialize file bytes (Redis stores as int arrays)
        reconstructed_files = [
            (bytes(byte_list), filename) for byte_list, filename in files_data
        ]

        # Step 3: Initialize workflow (worker-local instance)
        workflow = _build_workflow()

        # Step 4: Execute core grading pipeline
        report = run_async(workflow.run_pipeline(reconstructed_files))

        # Step 5: Persist results
        student_id = reconstructed_files[0][1] if reconstructed_files else task_id
        run_async(save_grading_result(db_path, task_id, student_id, report))
        run_async(update_task_status(db_path, task_id, "COMPLETED"))

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
        return {"status": "rejected", "reason": str(e)}

    except Exception as e:
        # Transient failure: Retry logic
        logger.error(f"[Worker] Task {task_id} failed (attempt {self.request.retries + 1}): {e}")
        run_async(update_task_status(db_path, task_id, "FAILED", error=str(e)))

        # Retry if attempts remain
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        # Permanent failure after max retries
        logger.critical(f"[Worker] Task {task_id} permanently failed after {self.max_retries} retries")
        return {"status": "failed", "error": str(e)}
