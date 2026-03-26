"""
Phase 30: Celery Beat Configuration - Scheduled Tasks

Replaces nohup daemon pattern with production-grade distributed scheduler.

Usage:
    celery -A src.worker.beat beat --loglevel=info
"""
import asyncio
import logging
from typing import Dict, Any

from celery.schedules import crontab

from src.worker.main import app
from src.db.client import update_task_status
from src.api.dependencies import get_db_path


logger = logging.getLogger(__name__)


@app.task(name="zombie_sweeper_task")
def zombie_sweeper_task(timeout_seconds: int = 600) -> Dict[str, Any]:
    """
    Celery Beat Task: Detect and mark zombie tasks stuck in PROCESSING state.
    
    Replaces standalone zombie_sweeper.py daemon script.
    Runs on scheduled interval via Celery Beat.
    
    Args:
        timeout_seconds: Zombie detection threshold (default: 10 minutes)
        
    Returns:
        Dict with sweep results
    """
    logger.info(f"[ZombieSweeper] Starting sweep with {timeout_seconds}s timeout")
    
    db_path = get_db_path()
    
    # Import here to avoid circular dependencies
    import aiosqlite
    
    async def sweep():
        async with aiosqlite.connect(db_path) as db:
            # Query stuck tasks
            query = """
                SELECT task_id, updated_at, 
                       (julianday('now') - julianday(updated_at)) * 86400 AS elapsed_seconds
                FROM tasks
                WHERE status = 'PROCESSING'
                  AND (julianday('now') - julianday(updated_at)) * 86400 > ?
            """
            
            async with db.execute(query, (timeout_seconds,)) as cursor:
                rows = await cursor.fetchall()
            
            zombie_count = len(rows)
            
            if zombie_count == 0:
                logger.info("[ZombieSweeper] No zombies detected")
                return {"zombies_found": 0, "zombies_cleaned": 0}
            
            logger.warning(f"[ZombieSweeper] Found {zombie_count} zombie(s)")
            for task_id, updated_at, elapsed in rows:
                logger.warning(f"  - {task_id}: stuck {int(elapsed)}s (last: {updated_at})")
            
            # Mark zombies as FAILED
            update_query = """
                UPDATE tasks
                SET status = 'FAILED',
                    error_message = 'Worker timeout: exceeded ' || ? || 's threshold (worker crash)',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'PROCESSING'
                  AND (julianday('now') - julianday(updated_at)) * 86400 > ?
            """
            
            await db.execute(update_query, (timeout_seconds, timeout_seconds))
            await db.commit()
            
            logger.info(f"[ZombieSweeper] Marked {zombie_count} zombie(s) as FAILED")
            return {"zombies_found": zombie_count, "zombies_cleaned": zombie_count}
    
    # Phase 30: Standard async bridge (explicit loop creation)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(sweep())
        return result
    finally:
        loop.close()


# Celery Beat Schedule Configuration
app.conf.beat_schedule = {
    'zombie-sweeper-every-minute': {
        'task': 'zombie_sweeper_task',
        'schedule': 60.0,  # Run every 60 seconds
        'args': (600,),    # 10-minute timeout threshold
    },
}

# Timezone configuration
app.conf.timezone = 'UTC'
