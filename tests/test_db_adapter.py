"""
Unit tests for database adapters.

Tests both SQLiteAdapter and PostgreSQLAdapter to ensure
they provide consistent behavior.
"""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from src.db.adapter import SQLiteAdapter, PostgreSQLAdapter, get_db_adapter


@pytest_asyncio.fixture
async def sqlite_adapter():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    adapter = SQLiteAdapter()
    await adapter.connect(db_path)

    # Create test table
    await adapter.execute("""
        CREATE TABLE test_table (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            value INTEGER
        )
    """)
    await adapter.commit()

    yield adapter

    await adapter.close()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest_asyncio.fixture
async def postgresql_adapter():
    """Create PostgreSQL adapter (requires running PostgreSQL)."""
    # Skip if PostgreSQL is not available
    postgresql_url = os.getenv("TEST_POSTGRESQL_URL")
    if not postgresql_url:
        pytest.skip("TEST_POSTGRESQL_URL not set")

    adapter = PostgreSQLAdapter()
    try:
        await adapter.connect(postgresql_url)
    except Exception:
        pytest.skip("PostgreSQL not available")

    # Create test table
    await adapter.execute("""
        CREATE TABLE IF NOT EXISTS test_table (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            value INTEGER
        )
    """)

    # Clean up existing data
    await adapter.execute("DELETE FROM test_table")

    yield adapter

    # Cleanup
    try:
        await adapter.execute("DROP TABLE IF EXISTS test_table")
        await adapter.close()
    except:
        pass


class TestSQLiteAdapter:
    """Test SQLite adapter functionality."""

    @pytest.mark.asyncio
    async def test_connect_and_close(self):
        """Test connection and disconnection."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        adapter = SQLiteAdapter()
        await adapter.connect(db_path)
        assert adapter.connection is not None

        await adapter.close()
        assert adapter.connection is None

        os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_execute(self, sqlite_adapter):
        """Test execute operation."""
        await sqlite_adapter.execute(
            "INSERT INTO test_table (id, name, value) VALUES (?, ?, ?)",
            (1, "test", 100)
        )
        await sqlite_adapter.commit()

        row = await sqlite_adapter.fetch_one("SELECT * FROM test_table WHERE id = ?", (1,))
        assert row is not None
        assert row["name"] == "test"
        assert row["value"] == 100

    @pytest.mark.asyncio
    async def test_fetch_one(self, sqlite_adapter):
        """Test fetch_one operation."""
        await sqlite_adapter.execute(
            "INSERT INTO test_table (id, name, value) VALUES (?, ?, ?)",
            (1, "test", 100)
        )
        await sqlite_adapter.commit()

        row = await sqlite_adapter.fetch_one("SELECT * FROM test_table WHERE id = ?", (1,))
        assert row is not None
        assert isinstance(row, dict)
        assert row["id"] == 1

        # Test non-existent row
        row = await sqlite_adapter.fetch_one("SELECT * FROM test_table WHERE id = ?", (999,))
        assert row is None

    @pytest.mark.asyncio
    async def test_fetch_all(self, sqlite_adapter):
        """Test fetch_all operation."""
        # Insert multiple rows
        await sqlite_adapter.executemany(
            "INSERT INTO test_table (id, name, value) VALUES (?, ?, ?)",
            [(1, "test1", 100), (2, "test2", 200), (3, "test3", 300)]
        )
        await sqlite_adapter.commit()

        rows = await sqlite_adapter.fetch_all("SELECT * FROM test_table ORDER BY id")
        assert len(rows) == 3
        assert rows[0]["name"] == "test1"
        assert rows[1]["name"] == "test2"
        assert rows[2]["name"] == "test3"

    @pytest.mark.asyncio
    async def test_executemany(self, sqlite_adapter):
        """Test executemany operation."""
        data = [(i, f"test{i}", i * 100) for i in range(1, 11)]
        await sqlite_adapter.executemany(
            "INSERT INTO test_table (id, name, value) VALUES (?, ?, ?)",
            data
        )
        await sqlite_adapter.commit()

        rows = await sqlite_adapter.fetch_all("SELECT COUNT(*) as count FROM test_table")
        assert rows[0]["count"] == 10


class TestPostgreSQLAdapter:
    """Test PostgreSQL adapter functionality."""

    @pytest.mark.asyncio
    async def test_connect_and_close(self):
        """Test connection and disconnection."""
        postgresql_url = os.getenv("TEST_POSTGRESQL_URL")
        if not postgresql_url:
            pytest.skip("TEST_POSTGRESQL_URL not set")

        adapter = PostgreSQLAdapter()
        await adapter.connect(postgresql_url)
        assert adapter.pool is not None

        await adapter.close()
        assert adapter.pool is None

    @pytest.mark.asyncio
    async def test_execute(self, postgresql_adapter):
        """Test execute operation."""
        await postgresql_adapter.execute(
            "INSERT INTO test_table (name, value) VALUES (?, ?)",
            ("test", 100)
        )

        row = await postgresql_adapter.fetch_one("SELECT * FROM test_table WHERE name = ?", ("test",))
        assert row is not None
        assert row["name"] == "test"
        assert row["value"] == 100

    @pytest.mark.asyncio
    async def test_placeholder_conversion(self, postgresql_adapter):
        """Test that ? placeholders are converted to $1, $2, etc."""
        await postgresql_adapter.execute(
            "INSERT INTO test_table (name, value) VALUES (?, ?)",
            ("test1", 100)
        )

        # Query with multiple placeholders
        await postgresql_adapter.execute(
            "INSERT INTO test_table (name, value) VALUES (?, ?)",
            ("test2", 200)
        )

        rows = await postgresql_adapter.fetch_all(
            "SELECT * FROM test_table WHERE value > ? ORDER BY value",
            (50,)
        )
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_fetch_one(self, postgresql_adapter):
        """Test fetch_one operation."""
        await postgresql_adapter.execute(
            "INSERT INTO test_table (name, value) VALUES (?, ?)",
            ("test", 100)
        )

        row = await postgresql_adapter.fetch_one("SELECT * FROM test_table WHERE name = ?", ("test",))
        assert row is not None
        assert isinstance(row, dict)
        assert row["name"] == "test"

        # Test non-existent row
        row = await postgresql_adapter.fetch_one("SELECT * FROM test_table WHERE name = ?", ("nonexistent",))
        assert row is None

    @pytest.mark.asyncio
    async def test_fetch_all(self, postgresql_adapter):
        """Test fetch_all operation."""
        # Insert multiple rows
        await postgresql_adapter.executemany(
            "INSERT INTO test_table (name, value) VALUES (?, ?)",
            [("test1", 100), ("test2", 200), ("test3", 300)]
        )

        rows = await postgresql_adapter.fetch_all("SELECT * FROM test_table ORDER BY value")
        assert len(rows) == 3
        assert rows[0]["name"] == "test1"
        assert rows[1]["name"] == "test2"
        assert rows[2]["name"] == "test3"

    @pytest.mark.asyncio
    async def test_executemany(self, postgresql_adapter):
        """Test executemany operation."""
        data = [(f"test{i}", i * 100) for i in range(1, 11)]
        await postgresql_adapter.executemany(
            "INSERT INTO test_table (name, value) VALUES (?, ?)",
            data
        )

        rows = await postgresql_adapter.fetch_all("SELECT COUNT(*) as count FROM test_table")
        assert rows[0]["count"] == 10


class TestGetDbAdapter:
    """Test get_db_adapter context manager."""

    @pytest.mark.asyncio
    async def test_sqlite_adapter(self):
        """Test getting SQLite adapter."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        async with get_db_adapter("sqlite", db_path) as adapter:
            assert isinstance(adapter, SQLiteAdapter)
            assert adapter.connection is not None

        os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_postgresql_adapter(self):
        """Test getting PostgreSQL adapter."""
        postgresql_url = os.getenv("TEST_POSTGRESQL_URL")
        if not postgresql_url:
            pytest.skip("TEST_POSTGRESQL_URL not set")

        async with get_db_adapter("postgresql", postgresql_url) as adapter:
            assert isinstance(adapter, PostgreSQLAdapter)
            assert adapter.pool is not None

    @pytest.mark.asyncio
    async def test_invalid_database_type(self):
        """Test that invalid database type raises error."""
        with pytest.raises(ValueError, match="Unsupported database type"):
            async with get_db_adapter("invalid", "connection_string") as adapter:
                pass


class TestPlaceholderConversion:
    """Test SQL placeholder conversion."""

    def test_no_placeholders(self):
        """Test query without placeholders."""
        query = "SELECT * FROM test_table"
        result = PostgreSQLAdapter._convert_placeholders(query)
        assert result == query

    def test_single_placeholder(self):
        """Test query with single placeholder."""
        query = "SELECT * FROM test_table WHERE id = ?"
        result = PostgreSQLAdapter._convert_placeholders(query)
        assert result == "SELECT * FROM test_table WHERE id = $1"

    def test_multiple_placeholders(self):
        """Test query with multiple placeholders."""
        query = "INSERT INTO test_table (name, value) VALUES (?, ?)"
        result = PostgreSQLAdapter._convert_placeholders(query)
        assert result == "INSERT INTO test_table (name, value) VALUES ($1, $2)"

    def test_complex_query(self):
        """Test complex query with multiple placeholders."""
        query = "SELECT * FROM test_table WHERE name = ? AND value > ? ORDER BY id LIMIT ?"
        result = PostgreSQLAdapter._convert_placeholders(query)
        assert result == "SELECT * FROM test_table WHERE name = $1 AND value > $2 ORDER BY id LIMIT $3"
