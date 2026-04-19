"""
Phase 30 / P9-07: Celery Beat Configuration - Scheduled Tasks

Replaces nohup daemon pattern with production-grade distributed scheduler.
P9-07 enhancement: Uses last_heartbeat_at for reliable PROCESSING zombie detection.

Usage:
    celery -A src.worker.beat beat --loglevel=info
"""
import asyncio
import logging
from typing import Dict, Any

from src.worker.main import app
from src.db.client import (
    fail_stale_pending_orphan_tasks,
    fail_stale_processing_tasks,
    list_stale_processing_tasks,
)
from src.api.dependencies import get_db_path
from src.core.config import settings


logger = logging.getLogger(__name__)


@app.task(name="zombie_sweeper_task")
def zombie_sweeper_task(timeout_seconds: int | None = None) -> Dict[str, Any]:
    """
    Celery Beat Task: Detect and mark zombie tasks stuck in PROCESSING state.

    P9-07: Now uses last_heartbeat_at (with updated_at fallback) for detection,
    and delegates cleanup to fail_stale_processing_tasks() DAO function.

    Args:
        timeout_seconds: Override detection threshold. Defaults to
                         settings.processing_orphan_timeout_seconds (600s).

    Returns:
        Dict with sweep results.
    """
    effective_timeout = timeout_seconds or settings.processing_orphan_timeout_seconds
    logger.info(f"[ZombieSweeper] Starting sweep with {effective_timeout}s timeout")

    db_path = get_db_path()

    async def sweep():
        # Phase 1: Log stale PROCESSING tasks for visibility
        stale = await list_stale_processing_tasks(
            db_path, timeout_seconds=effective_timeout, limit=200,
        )
        for row in stale:
            hb = row.get("last_heartbeat_at") or "(no heartbeat)"
            logger.warning(
                f"  - {row['task_id']}: stale {row.get('stale_seconds', '?')}s "
                f"(heartbeat: {hb}, updated: {row.get('updated_at')})"
            )

        # Phase 2: Mark stale PROCESSING tasks as FAILED (retryable)
        cleaned_processing = await fail_stale_processing_tasks(
            db_path, timeout_seconds=effective_timeout, limit=200,
        )
        processing_count = len(cleaned_processing)
        if processing_count:
            logger.warning(
                f"[ZombieSweeper] Marked {processing_count} stale PROCESSING task(s) as FAILED"
            )
        else:
            logger.info("[ZombieSweeper] No processing zombies detected")

        # Phase 3: Also clean stale orphan PENDING tasks
        pending_timeout = max(effective_timeout, settings.pending_orphan_timeout_seconds)
        cleaned_pending = await fail_stale_pending_orphan_tasks(
            db_path, timeout_seconds=pending_timeout, limit=500,
        )
        pending_count = len(cleaned_pending)
        if pending_count:
            logger.warning(
                f"[ZombieSweeper] Marked {pending_count} stale orphan PENDING task(s) as FAILED"
            )

        return {
            "zombies_found": len(stale),
            "zombies_cleaned": processing_count,
            "stale_pending_cleaned": pending_count,
        }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(sweep())
        return result
    finally:
        loop.close()


@app.task(name="upload_ttl_cleanup_task")
def upload_ttl_cleanup_task(ttl_days: int | None = None) -> Dict[str, Any]:
    """
    P9-04: Clean up expired upload directories.

    Scans the uploads directory for task folders older than *ttl_days*.
    Also marks associated completed tasks so the API can flag expired images.
    """
    effective_ttl = ttl_days or settings.upload_ttl_days
    uploads_path = settings.uploads_path
    logger.info(f"[UploadTTL] Scanning {uploads_path} for dirs older than {effective_ttl} days")

    if not uploads_path.is_dir():
        logger.info("[UploadTTL] Uploads directory does not exist, nothing to clean")
        return {"scanned": 0, "cleaned": 0, "errors": 0}

    import time as _time
    cutoff_ts = _time.time() - (effective_ttl * 86400)
    scanned = 0
    cleaned = 0
    errors = 0

    for entry in uploads_path.iterdir():
        if not entry.is_dir():
            continue
        scanned += 1
        try:
            dir_mtime = entry.stat().st_mtime
            if dir_mtime < cutoff_ts:
                import shutil
                shutil.rmtree(entry)
                cleaned += 1
                logger.info(f"[UploadTTL] Removed expired dir: {entry.name}")
        except Exception as exc:
            errors += 1
            logger.warning(f"[UploadTTL] Failed to remove {entry.name}: {exc}")

    logger.info(f"[UploadTTL] Done: scanned={scanned}, cleaned={cleaned}, errors={errors}")
    return {"scanned": scanned, "cleaned": cleaned, "errors": errors}


# Celery Beat Schedule Configuration
app.conf.beat_schedule = {
    'zombie-sweeper-every-minute': {
        'task': 'zombie_sweeper_task',
        'schedule': 60.0,
        'args': (),
    },
    'upload-ttl-cleanup-daily': {
        'task': 'upload_ttl_cleanup_task',
        'schedule': 86400.0,  # once per day
        'args': (),
    },
}

# Timezone configuration
app.conf.timezone = 'UTC'
