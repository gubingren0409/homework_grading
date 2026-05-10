from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import settings
from src.utils.sqlite_ops import backup_sqlite_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a consistent backup of the SQLite database.")
    parser.add_argument("--db", default=settings.sqlite_db_path, help="Path to the live SQLite database.")
    parser.add_argument("--out", default=None, help="Optional output backup path.")
    args = parser.parse_args()

    backup_path = backup_sqlite_database(args.db, args.out)
    print(backup_path)


if __name__ == "__main__":
    main()
