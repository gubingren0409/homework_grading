"""
Database initialization with adapter support.

This module provides database initialization that works with both
SQLite and PostgreSQL through the adapter layer.
"""
import logging
from pathlib import Path

from src.core.config import Settings
from src.db.adapter import get_db_adapter, DatabaseAdapter

logger = logging.getLogger(__name__)


async def init_db_with_adapter() -> None:
    """
    Initialize database using adapter layer.

    Supports both SQLite and PostgreSQL based on configuration.
    """
    settings = Settings()

    # Determine schema file based on database type
    if settings.database_type == "postgresql":
        schema_path = Path(__file__).parent / "schema_postgresql.sql"
    else:
        schema_path = Path(__file__).parent / "schema.sql"

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    schema_script = schema_path.read_text(encoding="utf-8")

    # Initialize database
    async with get_db_adapter(settings.database_type, settings.db_connection_string) as adapter:
        logger.info(f"Initializing {settings.database_type} database...")

        # Execute schema
        await adapter.executescript(schema_script)
        await adapter.commit()

        logger.info(f"Database initialized successfully")


async def check_db_connection() -> bool:
    """
    Check if database connection is working.

    Returns:
        True if connection is successful, False otherwise
    """
    settings = Settings()

    try:
        async with get_db_adapter(settings.database_type, settings.db_connection_string) as adapter:
            # Simple query to test connection
            if settings.database_type == "postgresql":
                result = await adapter.fetch_one("SELECT 1 as test")
            else:
                result = await adapter.fetch_one("SELECT 1 as test")

            return result is not None and result.get("test") == 1
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return False


async def get_database_info() -> dict:
    """
    Get database information.

    Returns:
        Dictionary with database type, version, and connection info
    """
    settings = Settings()

    info = {
        "type": settings.database_type,
        "connection_string": settings.db_connection_string,
    }

    try:
        async with get_db_adapter(settings.database_type, settings.db_connection_string) as adapter:
            if settings.database_type == "postgresql":
                version_row = await adapter.fetch_one("SELECT version()")
                info["version"] = version_row.get("version") if version_row else "unknown"
            else:
                version_row = await adapter.fetch_one("SELECT sqlite_version() as version")
                info["version"] = version_row.get("version") if version_row else "unknown"
    except Exception as e:
        logger.error(f"Failed to get database info: {e}")
        info["error"] = str(e)

    return info
