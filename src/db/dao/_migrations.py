"""Internal re-exports of schema migration helpers from client.py.

DAO modules import these instead of reaching into client.py directly,
keeping the dependency direction one-way: dao -> _migrations -> client.
"""
from src.db.client import (
    _ensure_tasks_columns,
    _ensure_rubrics_schema,
    _ensure_rubric_audit_schema,
    _ensure_domain_split_tables,
    _ensure_skill_validation_tables,
    _ensure_paper_grading_tables,
    _ensure_runtime_telemetry_table,
    _ensure_prompt_control_tables,
    _STATUS_WEIGHTS,
)

__all__ = [
    "_ensure_tasks_columns",
    "_ensure_rubrics_schema",
    "_ensure_rubric_audit_schema",
    "_ensure_domain_split_tables",
    "_ensure_skill_validation_tables",
    "_ensure_paper_grading_tables",
    "_ensure_runtime_telemetry_table",
    "_ensure_prompt_control_tables",
    "_STATUS_WEIGHTS",
]
