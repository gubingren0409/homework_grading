"""Tests for type helper utilities."""
import pytest
from src.api.utils.type_helpers import (
    safe_get_dict,
    safe_get_list,
    iter_dict_values,
    iter_list_items,
    filter_students_with_paper_report,
    extract_nested_path,
    ensure_dict,
    ensure_list,
)


class TestSafeGetDict:
    def test_valid_dict(self):
        data = {"key": {"nested": "value"}}
        result = safe_get_dict(data, "key")
        assert result == {"nested": "value"}

    def test_missing_key(self):
        data = {"other": "value"}
        result = safe_get_dict(data, "key")
        assert result == {}

    def test_non_dict_value(self):
        data = {"key": "string"}
        result = safe_get_dict(data, "key")
        assert result == {}

    def test_non_dict_data(self):
        result = safe_get_dict("not a dict", "key")
        assert result == {}

    def test_custom_default(self):
        data = {"key": "string"}
        result = safe_get_dict(data, "key", default={"default": "value"})
        assert result == {"default": "value"}


class TestSafeGetList:
    def test_valid_list(self):
        data = {"key": [1, 2, 3]}
        result = safe_get_list(data, "key")
        assert result == [1, 2, 3]

    def test_missing_key(self):
        data = {"other": "value"}
        result = safe_get_list(data, "key")
        assert result == []

    def test_non_list_value(self):
        data = {"key": "string"}
        result = safe_get_list(data, "key")
        assert result == []

    def test_non_dict_data(self):
        result = safe_get_list("not a dict", "key")
        assert result == []


class TestIterDictValues:
    def test_valid_dict_values(self):
        data = {"container": {"a": {"val": 1}, "b": {"val": 2}, "c": "skip"}}
        result = list(iter_dict_values(data, "container"))
        assert len(result) == 2
        assert {"val": 1} in result
        assert {"val": 2} in result

    def test_empty_container(self):
        data = {"container": {}}
        result = list(iter_dict_values(data, "container"))
        assert result == []

    def test_missing_key(self):
        data = {"other": {}}
        result = list(iter_dict_values(data, "container"))
        assert result == []


class TestIterListItems:
    def test_valid_list_items(self):
        data = {"items": [{"a": 1}, {"b": 2}, "skip", {"c": 3}]}
        result = list(iter_list_items(data, "items"))
        assert len(result) == 3
        assert {"a": 1} in result
        assert {"b": 2} in result
        assert {"c": 3} in result

    def test_empty_list(self):
        data = {"items": []}
        result = list(iter_list_items(data, "items"))
        assert result == []

    def test_type_filtering(self):
        data = {"items": ["a", "b", "c", 1, 2]}
        result = list(iter_list_items(data, "items", item_type=str))
        assert result == ["a", "b", "c"]


class TestFilterStudentsWithPaperReport:
    def test_valid_students(self):
        students = [
            {"id": "1", "paper_report": {"score": 100}},
            {"id": "2", "paper_report": "invalid"},
            {"id": "3", "paper_report": {"score": 90}},
            {"id": "4"},
        ]
        result = list(filter_students_with_paper_report(students))
        assert len(result) == 2
        student_ids = [s["id"] for s, _ in result]
        assert "1" in student_ids
        assert "3" in student_ids

    def test_empty_list(self):
        result = list(filter_students_with_paper_report([]))
        assert result == []


class TestExtractNestedPath:
    def test_valid_path(self):
        data = {"a": {"b": {"c": "value"}}}
        result = extract_nested_path(data, "a", "b", "c", expected_type=str)
        assert result == "value"

    def test_missing_intermediate_key(self):
        data = {"a": {"x": "value"}}
        result = extract_nested_path(data, "a", "b", "c")
        assert result is None

    def test_type_mismatch(self):
        data = {"a": {"b": {"c": "string"}}}
        result = extract_nested_path(data, "a", "b", "c", expected_type=int)
        assert result is None

    def test_single_key(self):
        data = {"key": {"nested": "value"}}
        result = extract_nested_path(data, "key")
        assert result == {"nested": "value"}


class TestEnsureDict:
    def test_valid_dict(self):
        result = ensure_dict({"key": "value"})
        assert result == {"key": "value"}

    def test_non_dict(self):
        assert ensure_dict("string") == {}
        assert ensure_dict(123) == {}
        assert ensure_dict(None) == {}
        assert ensure_dict([1, 2, 3]) == {}


class TestEnsureList:
    def test_valid_list(self):
        result = ensure_list([1, 2, 3])
        assert result == [1, 2, 3]

    def test_non_list(self):
        assert ensure_list("string") == []
        assert ensure_list(123) == []
        assert ensure_list(None) == []
        assert ensure_list({"key": "value"}) == []
