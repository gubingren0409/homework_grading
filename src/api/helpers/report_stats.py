"""
Helper functions for paper report statistics and export.

This module contains functions for generating statistics, CSV exports,
and markdown reports from paper grading data.
"""
import csv
import io
from typing import Any, Dict, List

from src.api.utils import safe_get_dict, safe_get_list, filter_students_with_paper_report, iter_dict_values
from src.api.helpers.paper_report import paper_report_answered_question_ids


def paper_report_question_stats(
    students: list[dict[str, Any]],
    question_ids: list[str],
) -> list[dict[str, Any]]:
    """Generate statistics for each question across all students."""
    stats: list[dict[str, Any]] = []
    for question_id in question_ids:
        student_count = 0
        answered_count = 0
        review_count = 0
        fully_correct_count = 0
        total_deduction = 0.0
        for student, paper_report in filter_students_with_paper_report(students):
            per_question = safe_get_dict(paper_report, "per_question")
            item = per_question.get(str(question_id))
            if not isinstance(item, dict):
                continue
            student_count += 1
            if str(question_id) in paper_report_answered_question_ids(paper_report):
                answered_count += 1
            if bool(item.get("requires_human_review")):
                review_count += 1
            if item.get("is_fully_correct") is True:
                fully_correct_count += 1
            total_deduction += float(item.get("total_score_deduction") or 0.0)
        stats.append(
            {
                "question_id": question_id,
                "student_count": student_count,
                "answered_count": answered_count,
                "review_count": review_count,
                "fully_correct_count": fully_correct_count,
                "total_deduction": total_deduction,
            }
        )
    return stats


def paper_report_review_reason_counts(students: list[dict[str, Any]]) -> dict[str, int]:
    """Count occurrences of each review reason across all students."""
    counts: dict[str, int] = {}
    for student, paper_report in filter_students_with_paper_report(students):
        for item in iter_dict_values(paper_report, "per_question"):
            review_reasons = safe_get_list(item, "review_reasons")
            for reason in review_reasons:
                key = str(reason or "").strip()
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def paper_reports_csv(payload: dict[str, Any]) -> str:
    """Generate CSV export of paper grading reports."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    question_ids = [str(question_id) for question_id in payload.get("question_ids", [])]
    header = [
        "student_id",
        "task_id",
        "task_status",
        "total_score_deduction",
        "requires_human_review",
        "review_reasons",
    ]
    for question_id in question_ids:
        header.extend(
            [
                f"{question_id}_status",
                f"{question_id}_deduction",
                f"{question_id}_review",
                f"{question_id}_review_reasons",
            ]
        )
    writer.writerow(header)

    for student in payload.get("students", []):
        paper_report = student.get("paper_report")
        per_question = paper_report.get("per_question") if isinstance(paper_report, dict) else {}
        summary_review_reasons: list[str] = []
        if isinstance(per_question, dict):
            for item in per_question.values():
                if not isinstance(item, dict):
                    continue
                review_reasons = safe_get_list(item, "review_reasons")
                for reason in review_reasons:
                    key = str(reason or "").strip()
                    if key and key not in summary_review_reasons:
                        summary_review_reasons.append(key)

        row = [
            student.get("student_id", ""),
            student.get("task_id", ""),
            student.get("task_status", ""),
            paper_report.get("total_score_deduction", "") if isinstance(paper_report, dict) else "",
            paper_report.get("requires_human_review", "") if isinstance(paper_report, dict) else "",
            ";".join(summary_review_reasons),
        ]

        for question_id in question_ids:
            item = per_question.get(str(question_id)) if isinstance(per_question, dict) else {}
            if isinstance(item, dict):
                row.extend(
                    [
                        item.get("status", ""),
                        item.get("total_score_deduction", ""),
                        item.get("requires_human_review", ""),
                        ";".join(str(r) for r in safe_get_list(item, "review_reasons")),
                    ]
                )
            else:
                row.extend(["", "", "", ""])
        writer.writerow(row)

    return buffer.getvalue()


def paper_reports_markdown(payload: dict[str, Any]) -> str:
    """Generate markdown export of paper grading reports."""
    lines: list[str] = []
    lines.append("# 批改报告汇总\n")

    question_ids = payload.get("question_ids", [])
    students = payload.get("students", [])

    lines.append(f"**题目数量**: {len(question_ids)}")
    lines.append(f"**学生数量**: {len(students)}\n")

    if question_ids:
        lines.append("## 题目统计\n")
        stats = paper_report_question_stats(students, [str(qid) for qid in question_ids])
        for stat in stats:
            lines.append(f"### 题目 {stat['question_id']}\n")
            lines.append(f"- 作答人数: {stat['answered_count']}/{stat['student_count']}")
            lines.append(f"- 完全正确: {stat['fully_correct_count']}")
            lines.append(f"- 需要复核: {stat['review_count']}")
            lines.append(f"- 总扣分: {stat['total_deduction']:.2f}\n")

    review_counts = paper_report_review_reason_counts(students)
    if review_counts:
        lines.append("## 复核原因统计\n")
        for reason, count in review_counts.items():
            lines.append(f"- {reason}: {count}")
        lines.append("")

    return "\n".join(lines)
