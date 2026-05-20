"""
Database adapter layer for supporting both SQLite and PostgreSQL.

This module provides a unified interface for database operations,
allowing seamless switching between SQLite and PostgreSQL.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseAdapter(ABC):
    """Abstract base class for database adapters."""

    @abstractmethod
    async def connect(self, connection_string: str):
        """Establish database connection."""
        pass

    @abstractmethod
    async def close(self):
        """Close database connection."""
        pass

    @abstractmethod
    async def execute(self, query: str, params: Optional[Tuple] = None) -> None:
        """Execute a query without returning results."""
        pass

    @abstractmethod
    async def fetch_one(self, query: str, params: Optional[Tuple] = None) -> Optional[dict]:
        """Fetch a single row."""
        pass

    @abstractmethod
    async def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[dict]:
        """Fetch all rows."""
        pass

    @abstractmethod
    async def executemany(self, query: str, params_list: List[Tuple]) -> None:
        """Execute a query multiple times with different parameters."""
        pass

    @abstractmethod
    async def commit(self) -> None:
        """Commit the current transaction."""
        pass

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback the current transaction."""
        pass

    @abstractmethod
    async def executescript(self, script: str) -> None:
        """Execute a SQL script."""
        pass


class SQLiteAdapter(DatabaseAdapter):
    """SQLite database adapter using aiosqlite."""

    def __init__(self):
        self.connection: Optional[aiosqlite.Connection] = None

    async def connect(self, connection_string: str):
        """Connect to SQLite database."""
        self.connection = await aiosqlite.connect(connection_string)
        # Apply SQLite-specific pragmas
        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA synchronous=NORMAL;")
        await self.connection.execute("PRAGMA busy_timeout=5000;")

    async def close(self):
        """Close SQLite connection."""
        if self.connection:
            await self.connection.close()
            self.connection = None

    async def execute(self, query: str, params: Optional[Tuple] = None) -> None:
        """Execute a query."""
        if not self.connection:
            raise RuntimeError("Database not connected")
        await self.connection.execute(query, params or ())

    async def fetch_one(self, query: str, params: Optional[Tuple] = None) -> Optional[dict]:
        """Fetch a single row."""
        if not self.connection:
            raise RuntimeError("Database not connected")

        self.connection.row_factory = aiosqlite.Row
        async with self.connection.execute(query, params or ()) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[dict]:
        """Fetch all rows."""
        if not self.connection:
            raise RuntimeError("Database not connected")

        self.connection.row_factory = aiosqlite.Row
        async with self.connection.execute(query, params or ()) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def executemany(self, query: str, params_list: List[Tuple]) -> None:
        """Execute a query multiple times."""
        if not self.connection:
            raise RuntimeError("Database not connected")
        await self.connection.executemany(query, params_list)

    async def commit(self) -> None:
        """Commit transaction."""
        if not self.connection:
            raise RuntimeError("Database not connected")
        await self.connection.commit()

    async def rollback(self) -> None:
        """Rollback transaction."""
        if not self.connection:
            raise RuntimeError("Database not connected")
        await self.connection.rollback()

    async def executescript(self, script: str) -> None:
        """Execute a SQL script."""
        if not self.connection:
            raise RuntimeError("Database not connected")
        await self.connection.executescript(script)


class PostgreSQLAdapter(DatabaseAdapter):
    """PostgreSQL database adapter using asyncpg."""

    def __init__(self):
        self.pool = None
        self.connection = None

    async def connect(self, connection_string: str):
        """Connect to PostgreSQL database."""
        try:
            import asyncpg
        except ImportError:
            raise RuntimeError("asyncpg is not installed. Run: pip install asyncpg")

        # Create connection pool
        self.pool = await asyncpg.create_pool(
            connection_string,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
        logger.info("PostgreSQL connection pool created")

    async def close(self):
        """Close PostgreSQL connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("PostgreSQL connection pool closed")

    async def execute(self, query: str, params: Optional[Tuple] = None) -> None:
        """Execute a query."""
        if not self.pool:
            raise RuntimeError("Database not connected")

        # Convert ? placeholders to $1, $2, etc.
        query = self._convert_placeholders(query)

        async with self.pool.acquire() as conn:
            await conn.execute(query, *(params or ()))

    async def fetch_one(self, query: str, params: Optional[Tuple] = None) -> Optional[dict]:
        """Fetch a single row."""
        if not self.pool:
            raise RuntimeError("Database not connected")

        query = self._convert_placeholders(query)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, *(params or ()))
            return dict(row) if row else None

    async def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[dict]:
        """Fetch all rows."""
        if not self.pool:
            raise RuntimeError("Database not connected")

        query = self._convert_placeholders(query)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *(params or ()))
            return [dict(row) for row in rows]

    async def executemany(self, query: str, params_list: List[Tuple]) -> None:
        """Execute a query multiple times."""
        if not self.pool:
            raise RuntimeError("Database not connected")

        query = self._convert_placeholders(query)

        async with self.pool.acquire() as conn:
            await conn.executemany(query, params_list)

    async def commit(self) -> None:
        """Commit transaction (no-op for asyncpg, uses autocommit by default)."""
        pass

    async def rollback(self) -> None:
        """Rollback transaction (no-op for asyncpg in autocommit mode)."""
        pass

    async def executescript(self, script: str) -> None:
        """Execute a SQL script."""
        if not self.pool:
            raise RuntimeError("Database not connected")

        # Split script into individual statements
        statements = [s.strip() for s in script.split(';') if s.strip()]

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for statement in statements:
                    if statement:
                        await conn.execute(statement)

    @staticmethod
    def _convert_placeholders(query: str) -> str:
        """Convert SQLite ? placeholders to PostgreSQL $1, $2, etc."""
        parts = query.split('?')
        if len(parts) == 1:
            return query

        result = parts[0]
        for i, part in enumerate(parts[1:], 1):
            result += f'${i}' + part
        return result


@asynccontextmanager
async def get_db_adapter(database_type: str, connection_string: str) -> AsyncIterator[DatabaseAdapter]:
    """
    Context manager for database adapter.

    Args:
        database_type: "sqlite" or "postgresql"
        connection_string: Database connection string

    Yields:
        DatabaseAdapter instance
    """
    if database_type == "sqlite":
        adapter = SQLiteAdapter()
    elif database_type == "postgresql":
        adapter = PostgreSQLAdapter()
    else:
        raise ValueError(f"Unsupported database type: {database_type}")

    try:
        await adapter.connect(connection_string)
        yield adapter
    finally:
        await adapter.close()
