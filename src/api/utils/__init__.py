"""API utility functions."""
from .type_helpers import (
    safe_get_dict,
    safe_get_list,
    iter_dict_values,
    iter_list_items,
    filter_students_with_paper_report,
    extract_nested_path,
    ensure_dict,
    ensure_list,
)

__all__ = [
    "safe_get_dict",
    "safe_get_list",
    "iter_dict_values",
    "iter_list_items",
    "filter_students_with_paper_report",
    "extract_nested_path",
    "ensure_dict",
    "ensure_list",
]
