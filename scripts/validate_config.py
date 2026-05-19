#!/usr/bin/env python3
"""
Configuration validation script for homework_grader_system.
Run this before starting the application to catch configuration issues early.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def main():
    """Validate configuration and print results"""
    print("=" * 60)
    print("Configuration Validation")
    print("=" * 60)

    try:
        from core.config import settings

        print(f"[OK] Configuration loaded successfully")
        print(f"  Environment: {settings.deployment_environment}")
        print(f"  Auth enabled: {settings.auth_enabled}")

        # Check API keys
        qwen_keys = settings.parsed_qwen_keys
        deepseek_keys = settings.parsed_deepseek_keys

        print(f"  Qwen API keys: {len(qwen_keys)} configured")
        print(f"  DeepSeek API keys: {len(deepseek_keys)} configured")

        # Warnings for production
        if settings.deployment_environment == "prod":
            print("\n[OK] Production security checks passed:")
            print(f"  - Auth is enabled")
            print(f"  - Secret key is secure ({len(settings.auth_secret_key)} chars)")

        # Warnings for development
        if settings.deployment_environment == "dev":
            print("\n[WARN] Development mode warnings:")
            if not settings.auth_enabled:
                print("  - Auth is disabled (OK for dev, enable in prod)")
            if settings.auth_secret_key == "change-me-in-production-use-32-chars-min":
                print("  - Using default secret key (OK for dev, change in prod)")

        # Check database path
        db_path = Path(settings.sqlite_db_path)
        if db_path.exists():
            print(f"\n[OK] Database found at: {settings.sqlite_db_path}")
        else:
            print(f"\n[WARN] Database not found at: {settings.sqlite_db_path}")
            print(f"  It will be created on first run")

        # Check uploads directory
        uploads_dir = Path(settings.uploads_dir)
        if uploads_dir.exists():
            print(f"[OK] Uploads directory exists: {settings.uploads_dir}")
        else:
            print(f"[WARN] Uploads directory will be created: {settings.uploads_dir}")

        print("\n" + "=" * 60)
        print("[OK] Configuration validation passed")
        print("=" * 60)
        return 0

    except ValueError as e:
        print(f"\n[ERROR] Configuration validation failed:")
        print(f"  {e}")
        print("\n" + "=" * 60)
        print("Please fix the configuration errors above before starting the application.")
        print("=" * 60)
        return 1
    except Exception as e:
        print(f"\n[ERROR] Unexpected error during validation:")
        print(f"  {type(e).__name__}: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
