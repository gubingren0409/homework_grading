#!/usr/bin/env python3
"""
Refactor worker/main.py by extracting helper functions.

This script replaces helper function definitions with imports.
"""
import re
from pathlib import Path


def add_imports(content: str) -> str:
    """Add imports for extracted helper functions."""
    # Find the import section (after the initial imports)
    import_pattern = r'(from src\.worker\.runtime import.*?\n)'

    new_imports = '''from src.worker.task_helpers import (
    get_worker_task_loop as _get_worker_task_loop,
    parse_db_timestamp as _parse_db_timestamp,
    task_processing_is_fresh as _task_processing_is_fresh,
    run_coroutine_in_isolated_thread as _run_coroutine_in_isolated_thread,
    build_workflow as _build_workflow,
    build_paper_workflow as _build_paper_workflow,
)
'''

    # Add after the last src.worker import
    content = re.sub(import_pattern, r'\1' + new_imports, content)

    return content


def remove_functions(content: str) -> str:
    """Remove extracted helper function definitions."""

    functions_to_remove = [
        '_get_worker_task_loop',
        '_parse_db_timestamp',
        '_task_processing_is_fresh',
        '_run_coroutine_in_isolated_thread',
        '_build_workflow',
        '_build_paper_workflow',
    ]

    for func_name in functions_to_remove:
        # Pattern to match function definition and its body
        pattern = rf'^def {re.escape(func_name)}\([^)]*\).*?(?=\n(?:def |@app\.|$))'
        content = re.sub(pattern, '', content, flags=re.MULTILINE | re.DOTALL)

    # Clean up multiple blank lines
    content = re.sub(r'\n\n\n+', '\n\n', content)

    return content


def main():
    worker_py = Path('src/worker/main.py')

    if not worker_py.exists():
        print(f"Error: {worker_py} not found")
        return 1

    content = worker_py.read_text(encoding='utf-8')
    original_length = len(content)

    # Add imports
    content = add_imports(content)
    print("Added imports for helper functions")

    # Remove function definitions
    content = remove_functions(content)

    new_length = len(content)
    removed = original_length - new_length

    print(f"Removed {removed} characters ({removed // 80} approximate lines)")

    # Write back
    worker_py.write_text(content, encoding='utf-8')
    print(f"Updated {worker_py}")

    return 0


if __name__ == '__main__':
    exit(main())
