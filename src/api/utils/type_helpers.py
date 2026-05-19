"""
Type validation and data extraction utilities for API responses.

This module provides helper functions to safely extract and validate nested
dictionary structures, reducing boilerplate type checking code.
"""
from typing import Any, TypeVar, Callable, Iterator

T = TypeVar('T')


def safe_get_dict(data: Any, key: str, default: dict | None = None) -> dict:
    """
    Safely get a dictionary value from a data structure.

    Args:
        data: The data structure to extract from
        key: The key to look up
        default: Default value if key not found or value is not a dict

    Returns:
        The dictionary value, or default (empty dict if not specified)
    """
    if default is None:
        default = {}
    if not isinstance(data, dict):
        return default
    value = data.get(key)
    if not isinstance(value, dict):
        return default
    return value


def safe_get_list(data: Any, key: str, default: list | None = None) -> list:
    """
    Safely get a list value from a data structure.

    Args:
        data: The data structure to extract from
        key: The key to look up
        default: Default value if key not found or value is not a list

    Returns:
        The list value, or default (empty list if not specified)
    """
    if default is None:
        default = []
    if not isinstance(data, dict):
        return default
    value = data.get(key)
    if not isinstance(value, list):
        return default
    return value


def iter_dict_values(data: Any, key: str) -> Iterator[dict]:
    """
    Iterate over dictionary values, skipping non-dict items.

    Args:
        data: The data structure containing the dictionary
        key: The key to look up

    Yields:
        Dictionary values that pass isinstance check

    Example:
        for item in iter_dict_values(paper_report, "per_question"):
            process(item)
    """
    container = safe_get_dict(data, key)
    for value in container.values():
        if isinstance(value, dict):
            yield value


def iter_list_items(data: Any, key: str, item_type: type[T] = dict) -> Iterator[T]:
    """
    Iterate over list items, skipping items that don't match the type.

    Args:
        data: The data structure containing the list
        key: The key to look up
        item_type: Expected type of list items (default: dict)

    Yields:
        List items that pass isinstance check

    Example:
        for answer in iter_list_items(bundle, "answers"):
            process(answer)
    """
    items = safe_get_list(data, key)
    for item in items:
        if isinstance(item, item_type):
            yield item


def filter_students_with_paper_report(students: list[dict[str, Any]]) -> Iterator[tuple[dict, dict]]:
    """
    Filter students list to only those with valid paper_report.

    Args:
        students: List of student dictionaries

    Yields:
        Tuples of (student, paper_report) for valid entries

    Example:
        for student, paper_report in filter_students_with_paper_report(students):
            process(student, paper_report)
    """
    for student in students:
        if not isinstance(student, dict):
            continue
        paper_report = student.get("paper_report")
        if isinstance(paper_report, dict):
            yield student, paper_report


def extract_nested_path(data: Any, *keys: str, expected_type: type[T] = dict) -> T | None:
    """
    Extract a value from a nested dictionary path with type checking.

    Args:
        data: The root data structure
        *keys: Sequence of keys to traverse
        expected_type: Expected type of the final value

    Returns:
        The extracted value if all checks pass, None otherwise

    Example:
        per_question = extract_nested_path(student, "paper_report", "per_question")
        if per_question:
            process(per_question)
    """
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    if isinstance(current, expected_type):
        return current
    return None


def ensure_dict(value: Any) -> dict:
    """
    Ensure a value is a dictionary, returning empty dict if not.

    Args:
        value: The value to check

    Returns:
        The value if it's a dict, otherwise empty dict
    """
    return value if isinstance(value, dict) else {}


def ensure_list(value: Any) -> list:
    """
    Ensure a value is a list, returning empty list if not.

    Args:
        value: The value to check

    Returns:
        The value if it's a list, otherwise empty list
    """
    return value if isinstance(value, list) else []
