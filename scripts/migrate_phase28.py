"""
Database Migration: Add Celery Task ID Column (Phase 28)

Extends tasks table with celery_task_id field for task revocation support.
Safe to run multiple times (idempotent).
"""
import sqlite3
import sys
from pathlib import Path


def migrate_tasks_table(db_path: str) -> None:
    """Add celery_task_id column to tasks table if not exists."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check current schema
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if "celery_task_id" in columns:
            print("✅ celery_task_id column already exists. Migration skipped.")
            return
        
        # Add new column
        print("⏳ Adding celery_task_id column to tasks table...")
        cursor.execute("ALTER TABLE tasks ADD COLUMN celery_task_id TEXT")
        conn.commit()
        print("✅ Migration completed successfully.")
        
    except sqlite3.Error as e:
        print(f"❌ Migration failed: {e}", file=sys.stderr)
        conn.rollback()
        raise
    
    finally:
        conn.close()


if __name__ == "__main__":
    # Default database path
    db_path = Path(__file__).parent.parent.parent / "outputs" / "grading_database.db"
    
    if len(sys.argv) > 1:
        db_path = Path(sys.argv[1])
    
    print(f"Target database: {db_path}")
    
    if not db_path.exists():
        print(f"⚠️  Database not found at {db_path}. Will be created on first init_db() call.")
        sys.exit(0)
    
    migrate_tasks_table(str(db_path))
