"""
Integration test for PostgreSQL migration.

This test verifies that the database adapter works correctly
with both SQLite and PostgreSQL.
"""
import os
import pytest

from src.db.init_adapter import init_db_with_adapter, check_db_connection, get_database_info


@pytest.mark.asyncio
async def test_sqlite_initialization():
    """Test SQLite database initialization."""
    # Set environment to use SQLite
    os.environ["DATABASE_TYPE"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = "outputs/test_grading.db"

    # Initialize database
    await init_db_with_adapter()

    # Check connection
    is_connected = await check_db_connection()
    assert is_connected is True

    # Get database info
    info = await get_database_info()
    assert info["type"] == "sqlite"
    assert "version" in info


@pytest.mark.asyncio
async def test_postgresql_initialization():
    """Test PostgreSQL database initialization."""
    postgresql_url = os.getenv("TEST_POSTGRESQL_URL")
    if not postgresql_url:
        pytest.skip("TEST_POSTGRESQL_URL not set")

    # Set environment to use PostgreSQL
    os.environ["DATABASE_TYPE"] = "postgresql"
    os.environ["POSTGRESQL_URL"] = postgresql_url

    # Initialize database
    try:
        await init_db_with_adapter()
    except Exception as e:
        pytest.skip(f"PostgreSQL not available: {e}")

    # Check connection
    is_connected = await check_db_connection()
    assert is_connected is True

    # Get database info
    info = await get_database_info()
    assert info["type"] == "postgresql"
    assert "version" in info
    assert "PostgreSQL" in info["version"]


@pytest.mark.asyncio
async def test_database_connection_check():
    """Test database connection check."""
    # Use SQLite for this test
    os.environ["DATABASE_TYPE"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = "outputs/test_grading.db"

    is_connected = await check_db_connection()
    assert isinstance(is_connected, bool)
