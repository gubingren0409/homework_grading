#!/usr/bin/env python3
"""
Migrate data from SQLite to PostgreSQL.

This script reads all data from the SQLite database and writes it to PostgreSQL,
preserving all relationships and data integrity.
"""
import asyncio
import logging
import sys
from pathlib import Path
from typing import List, Tuple

import aiosqlite

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import Settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Table migration order (respecting foreign key dependencies)
TABLES_TO_MIGRATE = [
    "tasks",
    "grading_results",
    "teacher_review_decisions",
    "hygiene_interception_log",
    "golden_annotation_assets",
    "task_runtime_telemetry",
    "prompt_control_state",
    "prompt_ab_configs",
    "prompt_ops_audit_log",
    "skill_validation_records",
    "paper_tasks",
    "paper_question_results",
    "rubrics",
    "rubric_generate_audit",
]


async def get_table_columns(db: aiosqlite.Connection, table_name: str) -> List[str]:
    """Get column names for a table."""
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        columns = await cursor.fetchall()
    return [col[1] for col in columns]


async def count_rows(db: aiosqlite.Connection, table_name: str) -> int:
    """Count rows in a table."""
    async with db.execute(f"SELECT COUNT(*) FROM {table_name}") as cursor:
        row = await cursor.fetchone()
    return row[0] if row else 0


async def migrate_table(
    sqlite_db: aiosqlite.Connection,
    pg_conn,
    table_name: str
) -> Tuple[int, int]:
    """
    Migrate a single table from SQLite to PostgreSQL.

    Returns:
        Tuple of (rows_read, rows_written)
    """
    logger.info(f"Migrating table: {table_name}")

    # Get column names
    columns = await get_table_columns(sqlite_db, table_name)
    if not columns:
        logger.warning(f"Table {table_name} has no columns, skipping")
        return 0, 0

    # Count rows
    row_count = await count_rows(sqlite_db, table_name)
    if row_count == 0:
        logger.info(f"Table {table_name} is empty, skipping")
        return 0, 0

    logger.info(f"Table {table_name} has {row_count} rows")

    # Read all rows from SQLite
    query = f"SELECT {', '.join(columns)} FROM {table_name}"
    async with sqlite_db.execute(query) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        return 0, 0

    # Prepare PostgreSQL INSERT statement
    placeholders = ', '.join([f'${i+1}' for i in range(len(columns))])
    insert_query = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"

    # Write to PostgreSQL
    rows_written = 0
    batch_size = 100

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        try:
            await pg_conn.executemany(insert_query, batch)
            rows_written += len(batch)
            if rows_written % 1000 == 0:
                logger.info(f"  Migrated {rows_written}/{row_count} rows")
        except Exception as e:
            logger.error(f"Error migrating batch {i}-{i+len(batch)}: {e}")
            raise

    logger.info(f"✓ Migrated {rows_written} rows from {table_name}")
    return len(rows), rows_written


async def verify_migration(sqlite_db: aiosqlite.Connection, pg_conn) -> bool:
    """Verify that all data was migrated correctly."""
    logger.info("\nVerifying migration...")

    all_match = True

    for table_name in TABLES_TO_MIGRATE:
        # Count rows in SQLite
        sqlite_count = await count_rows(sqlite_db, table_name)

        # Count rows in PostgreSQL
        pg_count = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")

        if sqlite_count == pg_count:
            logger.info(f"✓ {table_name}: {sqlite_count} rows (match)")
        else:
            logger.error(f"✗ {table_name}: SQLite={sqlite_count}, PostgreSQL={pg_count} (MISMATCH)")
            all_match = False

    return all_match


async def main():
    """Main migration function."""
    settings = Settings()

    # Check configuration
    if settings.database_type != "postgresql":
        logger.error("DATABASE_TYPE must be set to 'postgresql' in environment")
        return 1

    if not settings.postgresql_url:
        logger.error("POSTGRESQL_URL must be set in environment")
        return 1

    logger.info("="*80)
    logger.info("SQLite to PostgreSQL Migration")
    logger.info("="*80)
    logger.info(f"Source (SQLite): {settings.sqlite_db_path}")
    logger.info(f"Target (PostgreSQL): {settings.postgresql_url}")
    logger.info("")

    # Confirm migration
    response = input("This will migrate all data from SQLite to PostgreSQL. Continue? (yes/no): ")
    if response.lower() != "yes":
        logger.info("Migration cancelled")
        return 0

    try:
        # Import asyncpg
        import asyncpg
    except ImportError:
        logger.error("asyncpg is not installed. Run: pip install asyncpg")
        return 1

    # Connect to databases
    logger.info("\nConnecting to databases...")
    sqlite_db = await aiosqlite.connect(settings.sqlite_db_path)
    pg_conn = await asyncpg.connect(settings.postgresql_url)

    try:
        # Initialize PostgreSQL schema
        logger.info("\nInitializing PostgreSQL schema...")
        schema_path = Path(__file__).parent.parent / "src" / "db" / "schema_postgresql.sql"
        if not schema_path.exists():
            logger.error(f"Schema file not found: {schema_path}")
            return 1

        schema_script = schema_path.read_text(encoding="utf-8")
        await pg_conn.execute(schema_script)
        logger.info("✓ Schema initialized")

        # Migrate tables
        logger.info("\nMigrating tables...")
        total_read = 0
        total_written = 0

        for table_name in TABLES_TO_MIGRATE:
            try:
                read, written = await migrate_table(sqlite_db, pg_conn, table_name)
                total_read += read
                total_written += written
            except Exception as e:
                logger.error(f"Failed to migrate table {table_name}: {e}")
                raise

        logger.info(f"\n✓ Migration complete: {total_written} rows migrated")

        # Verify migration
        if await verify_migration(sqlite_db, pg_conn):
            logger.info("\n✓ Verification passed: All data migrated successfully")
            return 0
        else:
            logger.error("\n✗ Verification failed: Data mismatch detected")
            return 1

    except Exception as e:
        logger.error(f"\n✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        await sqlite_db.close()
        await pg_conn.close()
        logger.info("\nDatabase connections closed")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
