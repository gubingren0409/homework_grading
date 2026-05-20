"""
Helper functions for Celery worker tasks.

This module contains utility functions for task processing, workflow building,
and error handling in the worker.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from src.core.config import settings
from src.orchestration.workflow import GradingWorkflow
from src.orchestration.paper_workflow import PaperGradingWorkflow
from src.perception.factory import create_perception_engine
from src.cognitive.factory import create_cognitive_agent
from src.skills.service import SkillService


logger = logging.getLogger(__name__)


def get_worker_task_loop() -> asyncio.AbstractEventLoop:
    """Get or create event loop for worker tasks."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


def parse_db_timestamp(value: Any) -> datetime | None:
    """Parse database timestamp string to datetime object."""
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def task_processing_is_fresh(task: Dict[str, Any]) -> bool:
    """
    Check if task processing timestamp is recent enough to continue.

    Returns True if the task was marked as processing within the last 5 minutes,
    indicating it's safe to continue processing.
    """
    processing_at = parse_db_timestamp(task.get("processing_at"))
    if not processing_at:
        return False

    now = datetime.now(timezone.utc)
    age_seconds = (now - processing_at).total_seconds()

    # Consider fresh if less than 5 minutes old
    return age_seconds < 300


def run_coroutine_in_isolated_thread(coro):
    """
    Run a coroutine in an isolated thread with its own event loop.

    This is used when the Celery worker needs to run async code but
    doesn't have an event loop available.
    """
    import threading

    result = None
    exception = None

    def run_in_thread():
        nonlocal result, exception
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
        except Exception as e:
            exception = e
        finally:
            loop.close()

    thread = threading.Thread(target=run_in_thread)
    thread.start()
    thread.join()

    if exception:
        raise exception
    return result


def build_workflow() -> GradingWorkflow:
    """Build a standard grading workflow instance."""
    perception_engine = create_perception_engine()
    cognitive_agent = create_cognitive_agent()
    return GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
    )


def build_paper_workflow(db_path: str) -> PaperGradingWorkflow:
    """Build a paper grading workflow instance."""
    perception_engine = create_perception_engine()
    cognitive_agent = create_cognitive_agent()
    skill_service = SkillService(db_path=db_path) if settings.skill_service_enabled else None
    return PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
        skill_service=skill_service,
    )
