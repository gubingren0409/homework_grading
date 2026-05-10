from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import settings
from src.utils.sqlite_ops import restore_sqlite_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore the SQLite database from a backup file.")
    parser.add_argument("--backup", required=True, help="Path to the backup SQLite file.")
    parser.add_argument("--db", default=settings.sqlite_db_path, help="Target SQLite database path.")
    parser.add_argument("--force", action="store_true", help="Overwrite the target database if it exists.")
    args = parser.parse_args()

    restored_path = restore_sqlite_database(args.backup, args.db, overwrite=args.force)
    print(restored_path)


if __name__ == "__main__":
    main()
