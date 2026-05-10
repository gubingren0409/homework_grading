from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def backup_sqlite_database(db_path: str, backup_path: str | None = None) -> Path:
    source_path = Path(db_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    if backup_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = source_path.with_name(f"{source_path.stem}_backup_{timestamp}{source_path.suffix}")
    else:
        target_path = Path(backup_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(source_path) as source_conn, sqlite3.connect(target_path) as target_conn:
        source_conn.backup(target_conn)
    return target_path


def restore_sqlite_database(backup_path: str, db_path: str, *, overwrite: bool = False) -> Path:
    source_path = Path(backup_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite backup not found: {source_path}")

    target_path = Path(db_path).resolve()
    if target_path.exists() and not overwrite:
        raise FileExistsError(f"Target database already exists: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()

    with sqlite3.connect(source_path) as source_conn, sqlite3.connect(target_path) as target_conn:
        source_conn.backup(target_conn)
    return target_path
