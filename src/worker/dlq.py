"""
Phase 32: Dead Letter Queue (DLQ) Management

Tools for inspecting, replaying, and purging poison messages from the DLQ.
Poison messages are tasks that crashed after max retries, indicating:
- Code bugs (unhandled exceptions)
- Data corruption (malformed payloads)
- Infrastructure failures (storage unavailable)

DLQ Contents:
- task_id: Business UUID
- payload: Original Celery payload
- error: Final exception message
- failed_at: ISO timestamp
- retry_count: Number of retries before failure
"""
import json
import logging
from typing import List, Dict, Any, Optional
import redis

from src.core.config import settings
from src.worker.main import DLQ_QUEUE_NAME, grade_homework_task


logger = logging.getLogger(__name__)


def get_dlq_stats() -> Dict[str, Any]:
    """
    Get statistics about Dead Letter Queue.
    
    Returns:
        Dict with queue length and sample entries
    """
    redis_client = redis.from_url(settings.redis_url)
    
    queue_length = redis_client.llen(DLQ_QUEUE_NAME)
    
    # Get first 5 entries for preview (without removing)
    sample_entries = []
    if queue_length > 0:
        raw_entries = redis_client.lrange(DLQ_QUEUE_NAME, 0, 4)
        sample_entries = [json.loads(entry) for entry in raw_entries]
    
    return {
        "queue_name": DLQ_QUEUE_NAME,
        "total_items": queue_length,
        "sample_entries": sample_entries,
    }


def inspect_dlq_task(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Inspect a specific task in DLQ by task_id.
    
    Args:
        task_id: Business task UUID
    
    Returns:
        DLQ entry dict or None if not found
    """
    redis_client = redis.from_url(settings.redis_url)
    
    # Scan entire DLQ for matching task_id
    queue_length = redis_client.llen(DLQ_QUEUE_NAME)
    
    for i in range(queue_length):
        raw_entry = redis_client.lindex(DLQ_QUEUE_NAME, i)
        entry = json.loads(raw_entry)
        
        if entry.get("task_id") == task_id:
            return entry
    
    return None


def replay_dlq_task(task_id: str, remove_from_dlq: bool = True) -> str:
    """
    Replay a failed task from DLQ by re-enqueueing it.
    
    Args:
        task_id: Business task UUID
        remove_from_dlq: Whether to remove from DLQ after replay
    
    Returns:
        New Celery task ID
    
    Raises:
        ValueError: If task not found in DLQ
    """
    redis_client = redis.from_url(settings.redis_url)
    
    # Find task in DLQ
    queue_length = redis_client.llen(DLQ_QUEUE_NAME)
    target_entry = None
    target_index = -1
    
    for i in range(queue_length):
        raw_entry = redis_client.lindex(DLQ_QUEUE_NAME, i)
        entry = json.loads(raw_entry)
        
        if entry.get("task_id") == task_id:
            target_entry = entry
            target_index = i
            break
    
    if not target_entry:
        raise ValueError(f"Task {task_id} not found in DLQ")
    
    # Re-enqueue task
    payload = target_entry["payload"]
    db_path = target_entry["db_path"]
    
    celery_result = grade_homework_task.apply_async(
        args=[task_id, payload, db_path],
        task_id=task_id,  # Reuse same task_id
    )
    
    # Remove from DLQ if requested
    if remove_from_dlq:
        # Remove specific entry by value (Redis LREM)
        redis_client.lrem(DLQ_QUEUE_NAME, 1, json.dumps(target_entry))
        logger.info(f"[DLQ] Task {task_id} replayed and removed from DLQ")
    else:
        logger.info(f"[DLQ] Task {task_id} replayed but kept in DLQ")
    
    return celery_result.id


def purge_dlq(confirm: bool = False) -> int:
    """
    Delete all tasks from DLQ.
    
    Args:
        confirm: Must be True to actually purge
    
    Returns:
        Number of tasks purged
    
    Raises:
        RuntimeError: If confirm=False (safety check)
    """
    if not confirm:
        raise RuntimeError(
            "Purging DLQ requires explicit confirmation. "
            "Call purge_dlq(confirm=True) to proceed."
        )
    
    redis_client = redis.from_url(settings.redis_url)
    
    queue_length = redis_client.llen(DLQ_QUEUE_NAME)
    redis_client.delete(DLQ_QUEUE_NAME)
    
    logger.warning(f"[DLQ] Purged {queue_length} tasks from DLQ")
    return queue_length


def list_dlq_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    """
    List all tasks in DLQ.
    
    Args:
        limit: Maximum number of tasks to return
    
    Returns:
        List of DLQ entry dicts
    """
    redis_client = redis.from_url(settings.redis_url)
    
    raw_entries = redis_client.lrange(DLQ_QUEUE_NAME, 0, limit - 1)
    entries = [json.loads(entry) for entry in raw_entries]
    
    return entries


# CLI Commands for DLQ Management
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m src.worker.dlq stats")
        print("  python -m src.worker.dlq list [limit]")
        print("  python -m src.worker.dlq inspect <task_id>")
        print("  python -m src.worker.dlq replay <task_id>")
        print("  python -m src.worker.dlq purge")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "stats":
        stats = get_dlq_stats()
        print(f"DLQ Stats:")
        print(f"  Queue: {stats['queue_name']}")
        print(f"  Total Items: {stats['total_items']}")
        if stats['sample_entries']:
            print(f"\nSample Entries:")
            for entry in stats['sample_entries']:
                print(f"  - Task: {entry['task_id']}")
                print(f"    Error: {entry['error'][:100]}")
                print(f"    Failed At: {entry['failed_at']}")
    
    elif command == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        tasks = list_dlq_tasks(limit)
        print(f"DLQ Tasks ({len(tasks)}):")
        for task in tasks:
            print(f"  - {task['task_id']} | {task['failed_at']} | {task['error'][:50]}")
    
    elif command == "inspect":
        if len(sys.argv) < 3:
            print("Error: task_id required")
            sys.exit(1)
        
        task_id = sys.argv[2]
        entry = inspect_dlq_task(task_id)
        
        if entry:
            print(json.dumps(entry, indent=2))
        else:
            print(f"Task {task_id} not found in DLQ")
    
    elif command == "replay":
        if len(sys.argv) < 3:
            print("Error: task_id required")
            sys.exit(1)
        
        task_id = sys.argv[2]
        new_task_id = replay_dlq_task(task_id, remove_from_dlq=True)
        print(f"Task {task_id} replayed as {new_task_id}")
    
    elif command == "purge":
        response = input("Are you sure you want to purge ALL tasks from DLQ? (yes/no): ")
        if response.lower() == "yes":
            count = purge_dlq(confirm=True)
            print(f"Purged {count} tasks from DLQ")
        else:
            print("Purge cancelled")
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
