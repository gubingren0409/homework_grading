#!/usr/bin/env python3
"""
Split grade.py into 6 route files by functionality.

This script automatically extracts routes and their dependencies.
"""
import re
from pathlib import Path
from typing import Dict, List, Set


# Route classification
ROUTE_GROUPS = {
    "single": [
        "/grade/submit",
        "/grade/{task_id}",
    ],
    "paper": [
        "/grade/paper",
        "/grade/paper/submit",
        "/grade/paper/reports",
        "/grade/paper/inputs",
    ],
    "batch": [
        "/grade/submit-batch",
        "/grade/submit-batch-with-reference",
        "/grade-batch/{task_id}",
    ],
    "tasks": [
        "/tasks/{task_id}/stream",
        "/tasks/history",
        "/grade/{task_id}/cancel",
    ],
    "results": [
        "/results",
        "/results/{result_id}/inputs/{index}",
    ],
    "reports": [
        "/grade/flow-guide",
        "/grade/{task_id}/report",
        "/grade/{task_id}/insights",
    ],
}


def extract_route_function(content: str, route_path: str) -> tuple[str, str] | None:
    """Extract a route function and its decorator."""
    # Find the route decorator
    pattern = rf'(@router\.(get|post|put|delete)\("{re.escape(route_path)}".*?\n.*?async def \w+\(.*?\) -> .*?:.*?)(?=\n@router\.|$)'

    matches = list(re.finditer(pattern, content, re.DOTALL))
    if not matches:
        return None

    match = matches[0]
    route_code = match.group(1)

    # Extract function name
    func_match = re.search(r'async def (\w+)\(', route_code)
    if not func_match:
        return None

    func_name = func_match.group(1)

    # Find the complete function body
    start_pos = match.start()

    # Find where function ends (next @router or end of file)
    next_route = re.search(r'\n@router\.', content[match.end():])
    if next_route:
        end_pos = match.end() + next_route.start()
    else:
        end_pos = len(content)

    full_function = content[start_pos:end_pos].rstrip()

    return func_name, full_function


def create_route_file(group_name: str, routes: List[str], grade_content: str) -> str:
    """Create a route file for a specific group."""

    # Common imports
    imports = """import uuid
import json
import logging
import hashlib
import math
import csv
import io
import asyncio
import tempfile
import mimetypes
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import quote, urlparse, unquote

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Request, Response, BackgroundTasks
from fastapi.responses import FileResponse
from celery.exceptions import OperationalError as CeleryOperationalError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from src.api.dependencies import get_db_path, limiter
from src.api.sse import create_sse_response
from src.api.auth import TeacherIdentity, get_current_teacher
from src.api.helpers import (
    paper_report_answered_question_ids as _paper_report_answered_question_ids,
    paper_report_evidence_lookup as _paper_report_evidence_lookup,
    enrich_paper_report_evidence as _enrich_paper_report_evidence,
    paper_report_input_images as _paper_report_input_images,
    paper_report_crop_files as _paper_report_crop_files,
    base_paper_student_id as _base_paper_student_id,
    paper_report_question_stats as _paper_report_question_stats,
    paper_report_review_reason_counts as _paper_report_review_reason_counts,
    paper_reports_csv as _paper_reports_csv,
    paper_reports_markdown as _paper_reports_markdown,
)
from src.core.config import settings
from src.db.client import (
    create_task,
    update_task_celery_id,
    update_task_status,
    set_task_rubric_id,
    get_task,
    get_paper_task,
    fetch_results,
    fetch_results_by_task,
    list_paper_question_results,
    save_rubric,
    get_rubric_bundle,
    get_rubric,
    list_rubrics,
    get_recent_rubric_by_fingerprint,
    append_rubric_generate_audit,
    save_paper_grading_report,
)
from src.worker.main import grade_homework_task, app as celery_app
from src.core.storage_adapter import storage
from src.core.trace_context import get_trace_id
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.paper_workflow import PaperGradingWorkflow
from src.orchestration.rubric_selection import (
    parse_question_ids,
    select_rubric_bundle_questions,
)
from src.orchestration.workflow import GradingWorkflow
from src.perception.factory import create_perception_engine
from src.schemas.rubric_ir import RubricBundle, TeacherRubric
from src.skills.service import SkillService
from src.core.exceptions import GradingSystemError
from src.api.route_helpers import (
    best_effort_cleanup_stale_pending_orphans as _best_effort_cleanup_stale_pending_orphans,
    compute_source_fingerprint as _compute_source_fingerprint,
    derive_student_ids_from_filenames as _derive_student_ids_from_filenames,
    error_detail as _error_detail,
    request_client_ip as _request_client_ip,
    store_upload_file_with_limits as _store_upload_file_with_limits,
    build_task_insights as _build_task_insights,
    to_report_card as _to_report_card,
    validate_batch_single_page_file as _validate_batch_single_page_file,
    remove_task_from_celery_queue as _remove_task_from_celery_queue,
)
from src.api.sse import publish_task_status as _publish_task_status
from src.api.route_models import (
    GradeFlowGuideResponse,
    GradingResultItem,
    ReportCardItem,
    ReportDeductionItem,
    TaskHistoryItem,
    TaskHistoryResponse,
    TaskInsightHotspotItem,
    TaskInsightsResponse,
    TaskReportResponse,
    TaskResponse,
    TaskStatusResponse,
    LectureSuggestionItem,
    PaperGradeResponse,
)
from src.utils.file_parsers import UnsupportedFormatError, process_multiple_files


logger = logging.getLogger(__name__)
router = APIRouter()
_LOCAL_FALLBACK_REASON = "LOCAL_FALLBACK_SINGLE_NODE_ONLY"

"""

    # Extract helper functions from original grade.py
    helper_functions = extract_helper_functions(grade_content)

    # Extract routes
    route_functions = []
    for route_path in routes:
        result = extract_route_function(grade_content, route_path)
        if result:
            func_name, func_code = result
            route_functions.append(func_code)

    # Combine
    content = imports + "\n\n"

    if helper_functions:
        content += "# Helper Functions\n\n"
        content += helper_functions + "\n\n"

    content += "# Routes\n\n"
    content += "\n\n\n".join(route_functions)

    return content


def extract_helper_functions(content: str) -> str:
    """Extract helper functions that are not routes."""
    # Find all non-route functions
    pattern = r'^def _\w+\([^)]*\).*?(?=\n(?:def |async def |@router\.|$))'
    matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
    return "\n\n".join(matches) if matches else ""


def main():
    grade_py = Path("src/api/routers/grade.py")

    if not grade_py.exists():
        print(f"Error: {grade_py} not found")
        return 1

    content = grade_py.read_text(encoding='utf-8')

    print("Splitting grade.py into 6 route files...")

    for group_name, routes in ROUTE_GROUPS.items():
        print(f"\nCreating {group_name}.py with {len(routes)} routes...")

        route_content = create_route_file(group_name, routes, content)

        output_file = Path(f"src/api/routers/grade/{group_name}.py")
        output_file.write_text(route_content, encoding='utf-8')

        print(f"  Created {output_file} ({len(route_content)} chars)")

    print("\nCreating __init__.py...")
    init_content = """\"\"\"Grade API routes organized by functionality.\"\"\"
from fastapi import APIRouter

from .single import router as single_router
from .paper import router as paper_router
from .batch import router as batch_router
from .tasks import router as tasks_router
from .results import router as results_router
from .reports import router as reports_router

router = APIRouter()
router.include_router(single_router, tags=["grading"])
router.include_router(paper_router, tags=["paper-grading"])
router.include_router(batch_router, tags=["batch-grading"])
router.include_router(tasks_router, tags=["tasks"])
router.include_router(results_router, tags=["results"])
router.include_router(reports_router, tags=["reports"])

__all__ = ["router"]
"""

    init_file = Path("src/api/routers/grade/__init__.py")
    init_file.write_text(init_content, encoding='utf-8')

    print(f"  Created {init_file}")
    print("\nDone! Remember to update main.py to import from src.api.routers.grade")

    return 0


if __name__ == "__main__":
    exit(main())
