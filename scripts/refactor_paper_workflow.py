#!/usr/bin/env python3
"""
Replace extracted methods in paper_workflow.py with imported functions.

This script replaces method calls to extracted utility functions.
"""
import re
from pathlib import Path


def replace_method_calls(content: str) -> str:
    """Replace self._method() calls with imported function calls."""

    replacements = [
        # Runtime utils
        (r'self\._new_runtime_profile\(\)', 'new_runtime_profile()'),
        (r'self\._record_stage\(', 'record_stage('),
        (r'self\._append_runtime_span\(', 'append_runtime_span('),
        (r'self\._runtime_image_size\(', 'runtime_image_size('),
        (r'self\._stage_name_for_context_type\(', 'stage_name_for_context_type('),
        (r'self\._classify_runtime_error\(', 'classify_runtime_error('),
        (r'self\._first_non_empty\(', 'first_non_empty('),

        # Review reasons
        (r'self\._warning_requires_human_review\(', 'warning_requires_human_review('),
        (r'self\._review_reasons_from_answer_warnings\(', 'review_reasons_from_answer_warnings('),
        (r'self\._answer_requires_extraction_review\(', 'answer_requires_extraction_review('),
        (r'self\._review_reasons_from_report\(', 'review_reasons_from_report('),
        (r'self\._extend_question_review_reasons\(', 'extend_question_review_reasons('),
        (r'self\._merge_review_reasons\(', 'merge_review_reasons('),

        # Image processing
        (r'self\._image_chunk_plan\(', 'image_chunk_plan('),
        (r'self\._desired_answer_region_concurrency\(', 'desired_answer_region_concurrency('),
        (r'self\._perception_timeout_seconds_for_context_type\(', 'perception_timeout_seconds_for_context_type('),
    ]

    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content)

    return content


def remove_methods(content: str) -> str:
    """Remove extracted method definitions."""

    methods_to_remove = [
        '_new_runtime_profile',
        '_record_stage',
        '_append_runtime_span',
        '_runtime_image_size',
        '_stage_name_for_context_type',
        '_classify_runtime_error',
        '_first_non_empty',
        '_warning_requires_human_review',
        '_review_reasons_from_answer_warnings',
        '_answer_requires_extraction_review',
        '_review_reasons_from_report',
        '_extend_question_review_reasons',
        '_merge_review_reasons',
        '_image_chunk_plan',
        '_desired_answer_region_concurrency',
        '_perception_timeout_seconds_for_context_type',
    ]

    for method_name in methods_to_remove:
        # Pattern to match method definition and its body
        pattern = rf'^    def {re.escape(method_name)}\([^)]*\).*?(?=\n    (?:def |async def |$))'
        content = re.sub(pattern, '', content, flags=re.MULTILINE | re.DOTALL)

    # Clean up multiple blank lines
    content = re.sub(r'\n\n\n+', '\n\n', content)

    return content


def main():
    workflow_py = Path('src/orchestration/paper_workflow.py')

    if not workflow_py.exists():
        print(f"Error: {workflow_py} not found")
        return 1

    content = workflow_py.read_text(encoding='utf-8')
    original_length = len(content)

    # Replace method calls
    content = replace_method_calls(content)
    print("Replaced method calls with function calls")

    # Remove method definitions
    content = remove_methods(content)

    new_length = len(content)
    removed = original_length - new_length

    print(f"Removed {removed} characters ({removed // 80} approximate lines)")

    # Write back
    workflow_py.write_text(content, encoding='utf-8')
    print(f"Updated {workflow_py}")

    return 0


if __name__ == '__main__':
    exit(main())
