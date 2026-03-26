"""
Phase 29: Zombie Task Sweeper - Background Cleanup Daemon

Prevents state machine deadlock by detecting and marking tasks that are stuck
in PROCESSING state due to worker crashes (OOM, SIGKILL, network partition).

Usage:
    python scripts/zombie_sweeper.py --db-path outputs/grading_database.db --timeout 600
"""
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import aiosqlite


logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


async def sweep_zombie_tasks(
    db_path: str,
    timeout_seconds: int = 600,  # Default: 10 minutes
    dry_run: bool = False,
) -> int:
    """
    Detect and mark tasks stuck in PROCESSING state beyond timeout threshold.
    
    Args:
        db_path: Path to SQLite database
        timeout_seconds: Time threshold for zombie detection
        dry_run: If True, only log zombies without updating
        
    Returns:
        Number of zombie tasks found
    """
    async with aiosqlite.connect(db_path) as db:
        # Query tasks stuck in PROCESSING beyond timeout
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
            logger.info("✅ No zombie tasks detected.")
            return 0
        
        logger.warning(f"🧟 Detected {zombie_count} zombie task(s):")
        for task_id, updated_at, elapsed in rows:
            logger.warning(
                f"  - Task {task_id}: stuck for {int(elapsed)}s (last update: {updated_at})"
            )
        
        if dry_run:
            logger.info("🔍 Dry run mode: No updates performed.")
            return zombie_count
        
        # Mark zombies as FAILED with descriptive error
        update_query = """
            UPDATE tasks
            SET status = 'FAILED',
                error_message = 'Worker timeout: Task exceeded ' || ? || 's threshold (likely worker crash)',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'PROCESSING'
              AND (julianday('now') - julianday(updated_at)) * 86400 > ?
        """
        
        await db.execute(update_query, (timeout_seconds, timeout_seconds))
        await db.commit()
        
        logger.info(f"✅ Marked {zombie_count} zombie task(s) as FAILED.")
        return zombie_count


async def run_daemon(
    db_path: str,
    timeout_seconds: int,
    interval_seconds: int = 60,
):
    """
    Continuously monitor and clean zombie tasks.
    
    Args:
        db_path: Path to SQLite database
        timeout_seconds: Zombie detection threshold
        interval_seconds: Sweep interval
    """
    logger.info(f"🤖 Zombie Sweeper daemon started:")
    logger.info(f"   - Database: {db_path}")
    logger.info(f"   - Timeout: {timeout_seconds}s")
    logger.info(f"   - Interval: {interval_seconds}s")
    
    while True:
        try:
            await sweep_zombie_tasks(db_path, timeout_seconds, dry_run=False)
        except Exception as e:
            logger.error(f"❌ Sweep failed: {e}")
        
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Zombie Task Sweeper")
    parser.add_argument(
        "--db-path",
        type=str,
        default="outputs/grading_database.db",
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Zombie detection timeout in seconds (default: 600 = 10 minutes)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Sweep interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in daemon mode (continuous monitoring)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: detect zombies without updating",
    )
    
    args = parser.parse_args()
    
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error(f"❌ Database not found: {db_path}")
        sys.exit(1)
    
    if args.daemon:
        asyncio.run(run_daemon(str(db_path), args.timeout, args.interval))
    else:
        zombie_count = asyncio.run(
            sweep_zombie_tasks(str(db_path), args.timeout, args.dry_run)
        )
        sys.exit(0 if zombie_count == 0 else 1)
