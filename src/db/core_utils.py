import asyncio
import random
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiosqlite

from src.core.config import Settings


_WRITE_LOCK_MAX_RETRIES: int = 6
_WRITE_BACKOFF_BASE_SECONDS: float = 0.05
_WRITE_BACKOFF_MAX_SECONDS: float = 1.0
_SQLITE_BUSY_TIMEOUT_MS: int = 5000


async def _apply_connection_pragmas(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    await db.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS};")


@asynccontextmanager
async def _open_connection(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """
    Open SQLite connection with pragmas.

    Note: This is legacy function for SQLite only.
    For new code, use get_db_adapter() from src.db.adapter.
    """
    db = await aiosqlite.connect(db_path)
    try:
        await _apply_connection_pragmas(db)
        yield db
    finally:
        await db.close()


def _is_lock_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


async def _execute_write_with_retry(
    db_path: str,
    write_operation: Any,
) -> None:
    """
    Execute write operation with retry logic for SQLite.

    Note: This is legacy function for SQLite only.
    PostgreSQL does not need retry logic due to MVCC.
    """
    for attempt in range(1, _WRITE_LOCK_MAX_RETRIES + 1):
        try:
            async with _open_connection(db_path) as db:
                await write_operation(db)
                await db.commit()
                return
        except (aiosqlite.OperationalError, sqlite3.OperationalError) as exc:
            if not _is_lock_error(exc) or attempt == _WRITE_LOCK_MAX_RETRIES:
                raise

            backoff = min(
                _WRITE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                _WRITE_BACKOFF_MAX_SECONDS,
            )
            jitter = random.uniform(0.0, _WRITE_BACKOFF_BASE_SECONDS)
            await asyncio.sleep(backoff + jitter)


@asynccontextmanager
async def get_db_connection() -> AsyncIterator:
    """
    Get database connection based on configuration.

    Returns appropriate connection for SQLite or PostgreSQL.
    """
    settings = Settings()

    if settings.database_type == "postgresql":
        # Use PostgreSQL adapter
        from src.db.adapter import get_db_adapter
        async with get_db_adapter("postgresql", settings.postgresql_url) as adapter:
            yield adapter
    else:
        # Use SQLite (legacy)
        async with _open_connection(settings.sqlite_db_path) as db:
            yield db


