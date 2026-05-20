#!/usr/bin/env python3
"""
Remove extracted helper functions from grade.py.

This script removes function definitions that have been extracted to
src/api/helpers/ modules.
"""
import re
from pathlib import Path


def remove_function(content: str, func_name: str) -> str:
    """Remove a function definition from the content."""
    # Pattern to match function definition and its body
    pattern = rf'^def {re.escape(func_name)}\([^)]*\).*?(?=\n(?:def |async def |@router\.|class |$))'

    # Use MULTILINE and DOTALL flags
    result = re.sub(pattern, '', content, flags=re.MULTILINE | re.DOTALL)

    # Clean up multiple blank lines
    result = re.sub(r'\n\n\n+', '\n\n', result)

    return result


def main():
    grade_py = Path('src/api/routers/grade.py')

    if not grade_py.exists():
        print(f"Error: {grade_py} not found")
        return 1

    content = grade_py.read_text(encoding='utf-8')

    # Functions to remove (already extracted to helpers)
    functions_to_remove = [
        '_paper_report_answered_question_ids',
        '_paper_report_question_stats',
        '_paper_report_review_reason_counts',
        '_paper_reports_csv',
        '_paper_reports_markdown',
        '_base_paper_student_id',
        '_paper_report_evidence_lookup',
        '_enrich_paper_report_evidence',
        '_paper_report_input_images',
        '_paper_report_crop_files',
    ]

    original_length = len(content)

    for func_name in functions_to_remove:
        content = remove_function(content, func_name)
        print(f"Removed {func_name}")

    new_length = len(content)
    removed = original_length - new_length

    print(f"\nRemoved {removed} characters ({removed // 80} approximate lines)")

    # Write back
    grade_py.write_text(content, encoding='utf-8')
    print(f"Updated {grade_py}")

    return 0


if __name__ == '__main__':
    exit(main())
