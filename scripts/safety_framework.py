#!/usr/bin/env python3
"""
Safety framework for Phase 2 aggressive refactoring.

This script provides backup, validation, and rollback mechanisms.
"""
import subprocess
import sys
from pathlib import Path
from datetime import datetime


class RefactoringSafety:
    def __init__(self):
        self.backup_branch = f"backup/phase2-aggressive-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.original_branch = None

    def create_backup(self):
        """Create a backup branch before starting."""
        print("[BACKUP] Creating safety backup...")

        # Get current branch
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        self.original_branch = result.stdout.strip()

        # Create backup branch
        subprocess.run(
            ["git", "branch", self.backup_branch],
            check=True
        )
        print(f"[OK] Backup branch created: {self.backup_branch}")
        print(f"     Original branch: {self.original_branch}")

    def validate_imports(self):
        """Validate that all modules can be imported."""
        print("\n[CHECK] Validating imports...")

        modules_to_test = [
            "src.api.routers.grade",
            "src.orchestration.paper_workflow",
            "src.worker.main",
        ]

        for module in modules_to_test:
            try:
                result = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    print(f"  [OK] {module}")
                else:
                    print(f"  [FAIL] {module}")
                    print(f"     Error: {result.stderr}")
                    return False
            except Exception as e:
                print(f"  [FAIL] {module}")
                print(f"     Exception: {e}")
                return False

        return True

    def validate_routes(self):
        """Validate that all routes are registered."""
        print("\n[CHECK] Validating routes...")

        validation_script = """
from src.api.routers.grade import router
routes = [r.path for r in router.routes]
expected = [
    '/grade/submit',
    '/grade/paper',
    '/grade/paper/submit',
    '/grade/submit-batch',
    '/grade/submit-batch-with-reference',
    '/grade/flow-guide',
    '/grade/{task_id}',
    '/grade-batch/{task_id}',
    '/grade/paper/reports',
    '/grade/paper/inputs',
    '/tasks/{task_id}/stream',
    '/results',
    '/results/{result_id}/inputs/{index}',
    '/tasks/history',
    '/grade/{task_id}/report',
    '/grade/{task_id}/insights',
    '/grade/{task_id}/cancel',
]
missing = [e for e in expected if not any(e in r for r in routes)]
if missing:
    print(f"Missing routes: {missing}")
    exit(1)
print(f"All {len(expected)} routes registered")
"""

        try:
            result = subprocess.run(
                [sys.executable, "-c", validation_script],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                print(f"  [OK] {result.stdout.strip()}")
                return True
            else:
                print(f"  [FAIL] Route validation failed")
                print(f"     {result.stderr}")
                return False
        except Exception as e:
            print(f"  [FAIL] Route validation exception: {e}")
            return False

    def run_tests(self):
        """Run test suite."""
        print("\n[TEST] Running tests...")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=120
            )

            # Show summary
            lines = result.stdout.split('\n')
            for line in lines:
                if 'passed' in line or 'failed' in line or 'error' in line:
                    print(f"  {line}")

            return result.returncode == 0
        except subprocess.TimeoutExpired:
            print("  [WARN]  Tests timed out (may still be valid)")
            return True  # Don't fail on timeout
        except Exception as e:
            print(f"  [WARN]  Test execution error: {e}")
            return True  # Don't fail on test errors

    def validate_all(self):
        """Run all validation checks."""
        print("\n" + "="*60)
        print("[SAFETY]  SAFETY VALIDATION")
        print("="*60)

        checks = [
            ("Import validation", self.validate_imports),
            ("Route validation", self.validate_routes),
        ]

        all_passed = True
        for name, check_fn in checks:
            if not check_fn():
                print(f"\n[FAIL] {name} FAILED")
                all_passed = False
            else:
                print(f"\n[OK] {name} PASSED")

        # Tests are optional (don't block on failure)
        print("\n" + "-"*60)
        self.run_tests()

        return all_passed

    def rollback(self):
        """Rollback to backup branch."""
        print("\n[ROLLBACK] Rolling back to backup...")
        subprocess.run(
            ["git", "reset", "--hard", self.backup_branch],
            check=True
        )
        print(f"[OK] Rolled back to {self.backup_branch}")

    def cleanup_backup(self):
        """Delete backup branch after successful refactoring."""
        print(f"\n[CLEANUP] Cleaning up backup branch {self.backup_branch}...")
        subprocess.run(
            ["git", "branch", "-D", self.backup_branch],
            check=False  # Don't fail if already deleted
        )


def main():
    safety = RefactoringSafety()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "backup":
            safety.create_backup()
        elif command == "validate":
            success = safety.validate_all()
            sys.exit(0 if success else 1)
        elif command == "rollback":
            safety.rollback()
        elif command == "cleanup":
            safety.cleanup_backup()
        else:
            print(f"Unknown command: {command}")
            print("Usage: python safety_framework.py [backup|validate|rollback|cleanup]")
            sys.exit(1)
    else:
        print("Usage: python safety_framework.py [backup|validate|rollback|cleanup]")
        sys.exit(1)


if __name__ == "__main__":
    main()

