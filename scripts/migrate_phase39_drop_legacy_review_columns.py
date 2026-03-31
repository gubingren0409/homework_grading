"""
Phase 39 migration:
Physically remove legacy mixed-review fields from tasks table.
"""
import asyncio
import sys
from pathlib import Path

from src.db.client import migrate_drop_legacy_review_columns


async def main(db_path: str) -> None:
    await migrate_drop_legacy_review_columns(db_path)
    print(f"✅ Phase39 migration completed: {db_path}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("outputs/grading_database.db")
    asyncio.run(main(str(target)))
