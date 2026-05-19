#!/usr/bin/env python3
"""
Data directory cleanup script for homework_grader_system.
Backs up important data and cleans runtime files.
"""
import sys
import shutil
from pathlib import Path
from datetime import datetime

def main():
    """Clean up data directory with backup"""
    print("=" * 60)
    print("Data Directory Cleanup")
    print("=" * 60)

    project_root = Path(__file__).parent.parent
    uploads_dir = project_root / "data" / "uploads"
    backup_dir = project_root / "data" / "backups"

    # Check if uploads directory exists
    if not uploads_dir.exists():
        print(f"[INFO] Uploads directory does not exist: {uploads_dir}")
        return 0

    # Count files and size
    files = list(uploads_dir.rglob("*"))
    file_count = len([f for f in files if f.is_file()])
    total_size = sum(f.stat().st_size for f in files if f.is_file())
    size_mb = total_size / (1024 * 1024)

    print(f"\n[INFO] Current state:")
    print(f"  Files: {file_count}")
    print(f"  Total size: {size_mb:.2f} MB")

    if file_count == 0:
        print(f"\n[INFO] No files to clean")
        return 0

    # Ask for confirmation
    print(f"\n[WARN] This will delete all files in {uploads_dir}")
    print(f"[WARN] A backup will be created at {backup_dir}")
    response = input("\nProceed with cleanup? (yes/no): ").strip().lower()

    if response != "yes":
        print("[INFO] Cleanup cancelled")
        return 0

    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"uploads_backup_{timestamp}"

    try:
        print(f"\n[INFO] Creating backup...")
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(uploads_dir, backup_path)
        print(f"[OK] Backup created at: {backup_path}")

        # Clean uploads directory
        print(f"\n[INFO] Cleaning uploads directory...")
        shutil.rmtree(uploads_dir)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        print(f"[OK] Uploads directory cleaned")

        # Create .gitkeep to preserve directory structure
        gitkeep = uploads_dir / ".gitkeep"
        gitkeep.touch()
        print(f"[OK] Created .gitkeep file")

        print("\n" + "=" * 60)
        print(f"[OK] Cleanup completed successfully")
        print(f"  Freed space: {size_mb:.2f} MB")
        print(f"  Backup location: {backup_path}")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n[ERROR] Cleanup failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
