import uuid
import json
import logging
import hashlib
import math
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


def _derive_paper_student_id(files: List[UploadFile], explicit_student_id: Optional[str]) -> str:
    if explicit_student_id and explicit_student_id.strip():
        return explicit_student_id.strip()
    first_name = files[0].filename if files and files[0].filename else ""
    stem = Path(first_name).stem.strip()
    return stem or "paper-student"


def _json_response_safe(value: Any) -> Any:
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_json_response_safe(item) for item in value]
    if isinstance(value, dict):
        return {
            _json_response_safe(key): _json_response_safe(item)
            for key, item in value.items()
        }
    return value


def _paper_question_row_to_report_card(row: Dict[str, Any]) -> ReportCardItem:
    try:
        report = json.loads(row.get("report_json") or "{}")
    except Exception:
        report = {}
    if not isinstance(report, dict):
        report = {}

    deductions: List[ReportDeductionItem] = []
    suggestions: List[str] = []
    evidence_snippets: List[str] = []
    step_items = report.get("step_evaluations")
    if isinstance(step_items, list):
        for step in step_items:
            if not isinstance(step, dict) or bool(step.get("is_correct", False)):
                continue
            suggestion = str(step.get("correction_suggestion") or "").strip() or None
            evidence = str(step.get("evidence_snippet") or "").strip() or None
            deductions.append(
                ReportDeductionItem(
                    reference_element_id=str(step.get("reference_element_id") or "unknown"),
                    error_type=str(step.get("error_type") or "UNKNOWN"),
                    suggestion=suggestion,
                    evidence_snippet=evidence,
                )
            )
            if suggestion:
                suggestions.append(suggestion)
            if evidence:
                evidence_snippets.append(evidence)

    return ReportCardItem(
        result_id=int(row.get("id") or 0),
        student_id=f"{row.get('student_id') or 'paper-student'}｜{row.get('question_id') or '-'}",
        status=str(report.get("status") or row.get("status") or "SCORED"),
        is_pass=bool(report.get("is_fully_correct", float(row.get("total_deduction") or 0.0) <= 0.0)),
        total_deduction=float(report.get("total_score_deduction", row.get("total_deduction") or 0.0)),
        overall_feedback=str(report.get("overall_feedback") or ""),
        system_confidence=float(report.get("system_confidence", 0.0) or 0.0),
        requires_human_review=bool(report.get("requires_human_review", row.get("requires_human_review") or False)),
        deductions=deductions,
        evidence_snippets=list(dict.fromkeys(evidence_snippets)),
        suggestions=list(dict.fromkeys(suggestions)),
        input_images=[],
    )


def _base_paper_student_id(student_id: Any) -> str:
    value = str(student_id or "").strip()
    return re.sub(r"(?:_rerun)+$", "", value) or value


def _paper_report_evidence_lookup(paper_report: Dict[str, Any]) -> Dict[str, str]:
    bundle = paper_report.get("student_answer_bundle")
    if not isinstance(bundle, dict):
        return {}
    answers = bundle.get("answers")
    if not isinstance(answers, list):
        return {}

    lookup: Dict[str, str] = {}
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        question_id = str(answer.get("question_id") or "").strip()
        parts = answer.get("parts")
        if not question_id or not isinstance(parts, list):
            continue
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            elements = part.get("elements")
            if not isinstance(elements, list):
                continue
            for element_index, element in enumerate(elements):
                if not isinstance(element, dict):
                    continue
                element_id = str(element.get("element_id") or "").strip()
                raw = str(element.get("raw_content") or "").strip()
                if not element_id or not raw:
                    continue
                snippet = raw[:240]
                lookup[element_id] = snippet
                transformed_id = f"answer_{question_id}_part{part_index}_{element_index}_{element_id}"
                lookup[transformed_id] = snippet
                lookup[f"p0_{transformed_id}"] = snippet
    return lookup


def _enrich_paper_report_evidence(paper_report: Any) -> Any:
    if not isinstance(paper_report, dict):
        return paper_report
    evidence_lookup = _paper_report_evidence_lookup(paper_report)
    per_question = paper_report.get("per_question")
    if not evidence_lookup or not isinstance(per_question, dict):
        return paper_report
    for question_report in per_question.values():
        if not isinstance(question_report, dict):
            continue
        steps = question_report.get("step_evaluations")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or step.get("evidence_snippet"):
                continue
            ref_id = str(step.get("reference_element_id") or "").strip()
            if not ref_id:
                continue
            evidence = evidence_lookup.get(ref_id)
            if evidence is None:
                evidence = evidence_lookup.get(re.sub(r"^p\d+_", "", ref_id))
            if evidence:
                step["evidence_snippet"] = evidence
    return paper_report


def _paper_report_input_images(task_id: str, paper_report: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    refs_by_question = paper_report.get("input_file_refs_by_question")
    names_by_question = paper_report.get("input_filenames_by_question")
    if not isinstance(refs_by_question, dict):
        return {}
    if not isinstance(names_by_question, dict):
        names_by_question = {}

    images: Dict[str, List[Dict[str, str]]] = {}
    for question_id, refs in refs_by_question.items():
        if not isinstance(refs, list):
            continue
        question_key = str(question_id)
        names = names_by_question.get(question_key)
        if not isinstance(names, list):
            names = []
        image_items: List[Dict[str, str]] = []
        for idx, file_ref in enumerate(refs):
            if not isinstance(file_ref, str) or not file_ref.strip():
                continue
            parsed = urlparse(file_ref)
            fallback_name = Path(unquote(parsed.path or "")).name or f"question_input_{idx + 1}"
            name = names[idx] if idx < len(names) and isinstance(names[idx], str) else fallback_name
            image_items.append(
                {
                    "name": str(name or fallback_name),
                    "url": (
                        f"/api/v1/grade/paper/inputs?task_id={quote(task_id)}"
                        f"&question_id={quote(question_key, safe='')}&index={idx}"
                    ),
                }
            )
        if image_items:
            images[question_key] = image_items
    return images


def _local_file_response_from_ref(file_ref: str) -> FileResponse:
    parsed = urlparse(file_ref)
    if parsed.scheme != "file":
        raise HTTPException(status_code=400, detail="unsupported input asset scheme")

    raw_path = unquote(parsed.path or "")
    if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    asset_path = Path(raw_path).resolve()
    uploads_root = settings.uploads_path.resolve()
    try:
        asset_path.relative_to(uploads_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="input asset path rejected") from exc
    if not asset_path.exists():
        raise HTTPException(status_code=410, detail="input asset expired (TTL cleanup)")

    media_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
    return FileResponse(asset_path, media_type=media_type, filename=asset_path.name)


def _run_task_locally(task_id: str, payload: Dict[str, Any], db_path: str, trace_id: str) -> None:
    grade_homework_task.apply(
        args=[task_id, payload, db_path],
        task_id=task_id,
        headers={"trace_id": trace_id},
        throw=False,
    )


def _dispatch_grading_task(
    *,
    task_id: str,
    payload: Dict[str, Any],
    db_path: str,
    trace_id: str,
    background_tasks: BackgroundTasks,
) -> tuple[str, str]:
    # Pre-flight: verify Redis is reachable before attempting dispatch.
    # Without this, a down Redis may cause silent message loss.
    redis_ok, redis_err = _check_redis_health()
    if not redis_ok:
        logger.warning(
            "queue_dispatch_redis_unreachable",
            extra={
                "extra_fields": {
                    "task_id": task_id,
                    "event": "queue_dispatch_redis_unreachable",
                    "reason": redis_err,
                }
            },
        )
        # Fall back to local execution rather than dropping the task
        background_tasks.add_task(_run_task_locally, task_id, payload, db_path, trace_id)
        return f"local:{task_id}", "local_fallback"

    try:
        celery_result = grade_homework_task.apply_async(
            args=[task_id, payload, db_path],
            task_id=task_id,
            headers={"trace_id": trace_id},
            retry=False,
            ignore_result=True,
        )
        return celery_result.id, "celery"
    except (CeleryOperationalError, KombuOperationalError, RedisError, TimeoutError) as exc:
        logger.warning(
            "queue_dispatch_fallback_local",
            extra={
                "extra_fields": {
                    "task_id": task_id,
                    "event": "queue_dispatch_fallback_local",
                    "reason": str(exc),
                }
            },
        )
        background_tasks.add_task(_run_task_locally, task_id, payload, db_path, trace_id)
        return f"local:{task_id}", "local_fallback"


@router.post("/grade/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("100/minute")  # Phase 35: Increased for batch grading (100+ students)
async def submit_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    rubric_id: Optional[str] = Form(default=None),
    student_id: Optional[str] = Form(default=None),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    Phase 32: Storage Adapter Pattern - Backend-agnostic file handling.
    Phase 35: Batch-friendly rate limit (100/min) for large-scale grading.
    
    Returns HTTP 202 immediately after queueing task reference to Redis.
    File content stored via storage backend (LocalStorage/S3Storage).
    
    Contract Guarantees:
    - Response time < 50ms (no AI computation in HTTP lifecycle)
    - Task persisted to DB before queueing
    - Files written to storage backend before queueing
    - Worker receives file URIs (NOT file content)
    """
    await _best_effort_cleanup_stale_pending_orphans(db_path)

    # 1. Generate business task UUID
    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    
    # 2. Store uploaded files via storage adapter (Phase 32)
    file_refs = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)
    
    # 3. Pre-persist task state (PENDING) BEFORE queueing
    await create_task(db_path, task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    bound_rubric: Optional[Dict[str, Any]] = None
    if rubric_id:
        rubric_row = await get_rubric(db_path, rubric_id)
        if not rubric_row:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    error_code="RUBRIC_NOT_FOUND",
                    message="Rubric not found",
                    retryable=False,
                    next_action="select_valid_rubric",
                ),
            )
        rubric_raw = rubric_row.get("rubric_json")
        bound_rubric = json.loads(rubric_raw) if isinstance(rubric_raw, str) else rubric_raw
        await set_task_rubric_id(db_path, task_id, rubric_id)
    
    # 4. Dispatch to Celery worker queue (non-blocking)
    # Phase 32: Storage Adapter - enqueue URIs (backend-agnostic)
    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "single_submission"
    if student_id and student_id.strip():
        payload["student_id"] = student_id.strip()
    if bound_rubric is not None:
        payload["rubric_json"] = bound_rubric
    celery_task_id, dispatch_mode = _dispatch_grading_task(
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        trace_id=trace_id,
        background_tasks=background_tasks,
    )

    # 5. Track Celery task ID for potential revocation
    await update_task_celery_id(db_path, task_id, celery_task_id)
    
    # 6. Immediate HTTP 202 response (physical cutoff from computation)
    logger.info(
        "task_enqueued",
        extra={
            "extra_fields": {
                "task_id": task_id,
                "event": "task_enqueued",
                "dispatch_mode": dispatch_mode,
            }
        },
    )
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=rubric_id,
        mode="single_submission",
        submitted_count=len(file_refs),
        status_endpoint=f"/api/v1/grade/{task_id}",
        stream_endpoint=f"/api/v1/tasks/{task_id}/stream",
        suggested_poll_interval_seconds=2,
    )


@router.post("/grade/paper", response_model=PaperGradeResponse)
@limiter.limit("10/minute")
async def grade_whole_paper(
    request: Request,
    files: List[UploadFile] = File(...),
    bundle_id: str = Form(...),
    student_id: Optional[str] = Form(default=None),
    presegmented: bool = Form(default=False),
    question_ids: Optional[str] = Form(default=None),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    del request
    if not files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No files provided for paper grading.",
                retryable=False,
                next_action="adjust_file",
            ),
        )

    bundle_row = await get_rubric_bundle(db_path, bundle_id)
    if not bundle_row:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="RUBRIC_BUNDLE_NOT_FOUND",
                message="Rubric bundle not found",
                retryable=False,
                next_action="select_valid_rubric",
            ),
        )

    task_id = str(uuid.uuid4())
    paper_student_id = _derive_paper_student_id(files, student_id)
    await create_task(db_path, task_id, submitted_count=len(files), teacher_id=teacher.teacher_id)
    await update_task_status(db_path, task_id, "PROCESSING")

    files_data = []
    for file in files:
        content = await file.read()
        files_data.append((content, file.filename or "upload.bin"))

    try:
        image_bytes_list = await process_multiple_files(files_data)
        perception_engine = create_perception_engine()
        cognitive_agent = DeepSeekCognitiveEngine()
        workflow = PaperGradingWorkflow(
            perception_engine=perception_engine,
            cognitive_agent=cognitive_agent,
            skill_service=SkillService(db_path=db_path),
        )
        bundle = RubricBundle.model_validate(json.loads(bundle_row["bundle_json"]))
        requested_question_ids = parse_question_ids(question_ids)
        try:
            grading_bundle = select_rubric_bundle_questions(bundle, requested_question_ids)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=_error_detail(
                    error_code="QUESTION_IDS_INVALID",
                    message=str(exc),
                    retryable=False,
                    next_action="adjust_question_ids",
                ),
            ) from exc
        if presegmented:
            if len(image_bytes_list) != len(grading_bundle.rubrics):
                raise HTTPException(
                    status_code=422,
                    detail=_error_detail(
                        error_code="INPUT_REJECTED",
                        message="presegmented paper grading requires one uploaded image per selected rubric question.",
                        retryable=False,
                        next_action="adjust_file",
                    ),
                )
            report = await workflow.run_pipeline_with_presegmented_images(
                image_bytes_list,
                grading_bundle,
                presegmented_question_ids=[rubric.question_id for rubric in grading_bundle.rubrics],
            )
        else:
            report = await workflow.run_pipeline_with_preprocessed_images(image_bytes_list, grading_bundle)
        await save_paper_grading_report(
            db_path,
            task_id,
            paper_student_id,
            bundle_id,
            report,
        )
        await update_task_status(
            db_path,
            task_id,
            "COMPLETED",
            grading_status="SCORED",
            review_status="PENDING_REVIEW" if report.requires_human_review else "NOT_REQUIRED",
        )
    except UnsupportedFormatError as exc:
        await update_task_status(db_path, task_id, "FAILED", str(exc))
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message=str(exc),
                retryable=False,
                next_action="adjust_file",
            ),
        ) from exc
    except GradingSystemError as exc:
        await update_task_status(db_path, task_id, "FAILED", str(exc))
        error_text = str(exc)
        status_code = 503
        error_code = "UPSTREAM_UNAVAILABLE"
        next_action = "retry_upload"
        retryable = True
        if "LLM egress disabled by configuration" in error_text:
            error_code = "EGRESS_DISABLED"
            next_action = "enable_llm_egress"
            retryable = False
        raise HTTPException(
            status_code=status_code,
            detail=_error_detail(
                error_code=error_code,
                message=error_text,
                retryable=retryable,
                next_action=next_action,
            ),
        ) from exc
    except Exception as exc:
        await update_task_status(db_path, task_id, "FAILED", str(exc))
        raise

    return PaperGradeResponse(
        task_id=task_id,
        bundle_id=bundle_id,
        paper_id=report.paper_id,
        question_count=report.total_questions,
        report_json=report.model_dump(),
    )


@router.post("/grade/paper/submit", response_model=TaskResponse, status_code=202)
@limiter.limit("20/minute")
async def submit_whole_paper_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    bundle_id: str = Form(...),
    student_id: Optional[str] = Form(default=None),
    presegmented: bool = Form(default=False),
    question_ids: Optional[str] = Form(default=None),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    del request
    if not files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No files provided for paper grading.",
                retryable=False,
                next_action="adjust_file",
            ),
        )

    bundle_row = await get_rubric_bundle(db_path, bundle_id)
    if not bundle_row:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="RUBRIC_BUNDLE_NOT_FOUND",
                message="Rubric bundle not found",
                retryable=False,
                next_action="select_valid_rubric",
            ),
        )

    requested_question_ids = parse_question_ids(question_ids)
    grading_bundle = None
    if requested_question_ids or presegmented:
        bundle = RubricBundle.model_validate(json.loads(bundle_row["bundle_json"]))
        try:
            grading_bundle = select_rubric_bundle_questions(bundle, requested_question_ids)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=_error_detail(
                    error_code="QUESTION_IDS_INVALID",
                    message=str(exc),
                    retryable=False,
                    next_action="adjust_question_ids",
                ),
            ) from exc
    if presegmented:
        assert grading_bundle is not None
        if len(files) != len(grading_bundle.rubrics):
            raise HTTPException(
                status_code=422,
                detail=_error_detail(
                    error_code="INPUT_REJECTED",
                    message="presegmented paper grading requires one uploaded image per selected rubric question.",
                    retryable=False,
                    next_action="adjust_file",
                ),
            )

    await _best_effort_cleanup_stale_pending_orphans(db_path)

    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    paper_student_id = _derive_paper_student_id(files, student_id)

    file_refs: List[str] = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)

    await create_task(db_path, task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "paper_submission"
    payload["bundle_id"] = bundle_id
    payload["student_id"] = paper_student_id
    if requested_question_ids and grading_bundle is not None:
        payload["rubric_bundle_json"] = grading_bundle.model_dump()
        payload["selected_question_ids"] = [rubric.question_id for rubric in grading_bundle.rubrics]
    if presegmented:
        assert grading_bundle is not None
        payload["presegmented_question_ids"] = [
            rubric.question_id for rubric in grading_bundle.rubrics
        ]

    celery_task_id, dispatch_mode = _dispatch_grading_task(
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        trace_id=trace_id,
        background_tasks=background_tasks,
    )

    await update_task_celery_id(db_path, task_id, celery_task_id)
    logger.info(
        "task_enqueued",
        extra={
            "extra_fields": {
                "task_id": task_id,
                "event": "task_enqueued_paper",
                "dispatch_mode": dispatch_mode,
            }
        },
    )
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=None,
        mode="paper_submission",
        submitted_count=len(file_refs),
        status_endpoint=f"/api/v1/grade/{task_id}",
        stream_endpoint=f"/api/v1/tasks/{task_id}/stream",
        suggested_poll_interval_seconds=2,
    )


@router.post("/grade/submit-batch", response_model=TaskResponse, status_code=202)
@limiter.limit("60/minute")
async def submit_batch_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    rubric_id: Optional[str] = Form(default=None),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    Batch mode:
    - one uploaded image == one student submission
    - each file is graded independently under the same task_id
    """
    del request
    if not files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No files provided for batch grading.",
                retryable=False,
                next_action="adjust_file",
            ),
        )

    for upload in files:
        _validate_batch_single_page_file(upload)

    await _best_effort_cleanup_stale_pending_orphans(db_path)

    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    student_ids = _derive_student_ids_from_filenames(files)

    file_refs: List[str] = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)

    await create_task(db_path, task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    bound_rubric: Optional[Dict[str, Any]] = None
    if rubric_id:
        rubric_row = await get_rubric(db_path, rubric_id)
        if not rubric_row:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    error_code="RUBRIC_NOT_FOUND",
                    message="Rubric not found",
                    retryable=False,
                    next_action="select_valid_rubric",
                ),
            )
        rubric_raw = rubric_row.get("rubric_json")
        bound_rubric = json.loads(rubric_raw) if isinstance(rubric_raw, str) else rubric_raw
        await set_task_rubric_id(db_path, task_id, rubric_id)

    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "batch_single_page"
    payload["student_ids"] = student_ids
    if bound_rubric is not None:
        payload["rubric_json"] = bound_rubric

    celery_task_id, dispatch_mode = _dispatch_grading_task(
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        trace_id=trace_id,
        background_tasks=background_tasks,
    )

    await update_task_celery_id(db_path, task_id, celery_task_id)
    logger.info(
        "task_enqueued",
        extra={
            "extra_fields": {
                "task_id": task_id,
                "event": "task_enqueued_batch",
                "dispatch_mode": dispatch_mode,
            }
        },
    )
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=rubric_id,
        mode="batch_single_page",
        submitted_count=len(file_refs),
        status_endpoint=f"/api/v1/grade/{task_id}",
        stream_endpoint=f"/api/v1/tasks/{task_id}/stream",
        suggested_poll_interval_seconds=2,
    )


@router.post("/grade/submit-batch-with-reference", response_model=TaskResponse, status_code=202)
@limiter.limit("40/minute")
async def submit_batch_with_reference_grading_job(
    request: Request,
    background_tasks: BackgroundTasks,
    reference_files: List[UploadFile] = File(...),
    files: List[UploadFile] = File(...),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """
    One-shot smart batch mode:
    - upload reference + student answers in one request
    - worker auto-generates rubric and grades in a single task
    """
    del request
    if not reference_files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No reference files provided.",
                retryable=False,
                next_action="adjust_file",
            ),
        )
    if not files:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="No student files provided for batch grading.",
                retryable=False,
                next_action="adjust_file",
            ),
        )

    for upload in files:
        _validate_batch_single_page_file(upload)

    await _best_effort_cleanup_stale_pending_orphans(db_path)

    task_id = str(uuid.uuid4())
    trace_id = get_trace_id()
    student_ids = _derive_student_ids_from_filenames(files)

    file_refs: List[str] = []
    for file in files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        file_refs.append(file_ref)

    reference_file_refs: List[str] = []
    for file in reference_files:
        file_ref = await _store_upload_file_with_limits(task_id, file)
        reference_file_refs.append(file_ref)

    await create_task(db_path, task_id, submitted_count=len(file_refs), teacher_id=teacher.teacher_id)

    payload = storage.prepare_payload(file_refs)
    payload["mode"] = "batch_single_page"
    payload["student_ids"] = student_ids
    payload["reference_file_refs"] = reference_file_refs

    celery_task_id, dispatch_mode = _dispatch_grading_task(
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        trace_id=trace_id,
        background_tasks=background_tasks,
    )

    await update_task_celery_id(db_path, task_id, celery_task_id)
    logger.info(
        "task_enqueued",
        extra={
            "extra_fields": {
                "task_id": task_id,
                "event": "task_enqueued_batch_with_reference",
                "dispatch_mode": dispatch_mode,
            }
        },
    )
    return TaskResponse(
        task_id=task_id,
        status="PENDING",
        rubric_id=None,
        mode="batch_single_page",
        submitted_count=len(file_refs),
        status_endpoint=f"/api/v1/grade/{task_id}",
        stream_endpoint=f"/api/v1/tasks/{task_id}/stream",
        suggested_poll_interval_seconds=2,
    )


@router.get("/grade/flow-guide", response_model=GradeFlowGuideResponse)
async def get_grade_flow_guide():
    """
    学生任务台对接辅助接口：
    提供提交、轮询、SSE 与错误处理的最小状态机约定。
    """
    return GradeFlowGuideResponse(
        submit_endpoint="/api/v1/grade/submit",
        batch_submit_endpoint="/api/v1/grade/submit-batch",
        batch_submit_with_reference_endpoint="/api/v1/grade/submit-batch-with-reference",
        paper_submit_endpoint="/api/v1/grade/paper/submit",
        status_endpoint_template="/api/v1/grade/{task_id}",
        stream_endpoint_template="/api/v1/tasks/{task_id}/stream",
        task_status_enum=["PENDING", "PROCESSING", "COMPLETED", "FAILED"],
        terminal_statuses=["COMPLETED", "FAILED"],
        error_code_actions={
            "UPLOAD_TIMEOUT": "retry_upload",
            "FILE_TOO_LARGE": "adjust_file",
            "RATE_LIMITED": "wait_and_retry",
            "TASK_NOT_FOUND": "submit_new_task",
            "TASK_NOT_COMPLETED": "wait_for_completion",
            "INPUT_REJECTED": "retry_upload",
            "BATCH_FILE_TYPE_UNSUPPORTED": "adjust_file",
            "TASK_FAILED": "retry_upload",
            "INTERNAL_ERROR": "contact_support",
            "SSE_BACKEND_UNAVAILABLE": "fallback_to_polling",
            "UPSTREAM_UNAVAILABLE": "retry_upload",
        },
        notes=[
            "优先使用 SSE，若出现 SSE_BACKEND_UNAVAILABLE 则回退到状态轮询。",
            "状态轮询建议间隔 2 秒；可配合 ETag 条件请求降低带宽。",
            "FAILED 与 REJECTED_UNREADABLE 默认允许重新提交。",
            "队列后端不可用时，会自动回退到本地后台执行并继续返回 task_id。",
            "submit_endpoint 用于单学生提交（可多页）；batch_submit_endpoint 用于多学生单页批处理。",
            "paper_submit_endpoint 用于整卷学生作答异步提交；/grade/paper 保留同步直返报告路径。",
            "Rubric 生成走整页感知聚合链路，不依赖布局切片 gate。",
        ],
    )


@router.get("/grade/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_job_status_and_results(
    request: Request,
    response: Response,
    task_id: str,
    db_path: str = Depends(get_db_path)
):
    """
    Phase 32: HTTP cache negotiation with ETag/Last-Modified headers.
    
    Supports conditional requests (If-None-Match, If-Modified-Since) to reduce
    unnecessary data transfer when task state hasn't changed.
    
    Returns:
        - 200 OK: Full task status (with ETag and Last-Modified headers)
        - 304 Not Modified: Task state unchanged since last request
        - 404 Not Found: Task doesn't exist
        
    Benefits:
        - Eliminates duplicate payload transfer (200 OK → 304 Not Modified)
        - Reduces bandwidth by ~70% for unchanged status polls
        - Client can safely poll at higher frequency
    
    Rate limited to 30/min to prevent polling storms (prefer SSE for real-time).
    """
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )
    
    # Base response structure
    response_data = {
        "task_id": task["task_id"],
        "status": task["status"],
        "grading_status": task.get("grading_status"),
        "rubric_id": task.get("rubric_id"),
        "review_status": task.get("review_status"),
        "submitted_count": int(task.get("submitted_count") or 0),
        "fallback_reason": task.get("fallback_reason"),
        "retryable": False,
        "status_endpoint": f"/api/v1/grade/{task_id}",
        "stream_endpoint": f"/api/v1/tasks/{task_id}/stream",
        "suggested_poll_interval_seconds": 2,
    }

    if task["status"] in ["PENDING", "PROCESSING"]:
        uploaded_count = int(response_data.get("submitted_count") or 0)
        response_data.update(
            {
                "result_count": 0,
                "uploaded_count": uploaded_count,
                "processed_count": 0,
                "succeeded_count": 0,
                "rejected_count": 0,
                "progress": float(task.get("progress") or 0.0),
                "eta_seconds": int(task.get("eta_seconds") or 60),
                "next_action": "wait_for_completion",
            }
        )
        state_string = (
            f"{task['status']}_{task.get('updated_at', '')}_{task.get('error_message', '')}_"
            f"{response_data.get('submitted_count', 0)}_{response_data.get('result_count', 0)}"
        )
        etag = hashlib.md5(state_string.encode()).hexdigest()
        if request.headers.get("If-None-Match") == etag:
            response.status_code = 304
            response.headers["ETag"] = etag
            response.headers["Last-Modified"] = task.get("updated_at", "")
            return Response(status_code=304)
        response.headers["ETag"] = etag
        response.headers["Last-Modified"] = task.get("updated_at", "")
        response.headers["Cache-Control"] = "private, must-revalidate"
        return TaskStatusResponse(**response_data)

    from src.db.client import _open_connection, aiosqlite
    from src.db.dao._migrations import _ensure_paper_grading_tables

    async with _open_connection(db_path) as db:
        await _ensure_paper_grading_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                COUNT(1) AS total_results,
                SUM(CASE WHEN report_json LIKE '%"status": "REJECTED_UNREADABLE"%'
                          OR report_json LIKE '%"status":"REJECTED_UNREADABLE"%'
                         THEN 1 ELSE 0 END) AS rejected_results
            FROM grading_results WHERE task_id = ?
            """,
            (task_id,),
        ) as cursor:
            row = await cursor.fetchone()
            total_results = int((row["total_results"] if row else 0) or 0)
            rejected_results = int((row["rejected_results"] if row else 0) or 0)
        if total_results == 0:
            async with db.execute(
                """
                SELECT
                    COUNT(1) AS total_results,
                    SUM(CASE WHEN status = 'REJECTED_UNREADABLE' THEN 1 ELSE 0 END) AS rejected_results
                FROM paper_question_results WHERE task_id = ?
                """,
                (task_id,),
            ) as cursor:
                paper_row = await cursor.fetchone()
                total_results = int((paper_row["total_results"] if paper_row else 0) or 0)
                rejected_results = int((paper_row["rejected_results"] if paper_row else 0) or 0)
        # P8.5-04: 三段计数语义统一
        # uploaded = 教师提交份数；processed = 已落库结果数（含拒判占位）；
        # succeeded = 已落库结果中非拒判部分；rejected = 拒判数。
        uploaded_count = int(response_data.get("submitted_count") or 0)
        succeeded_results = max(0, total_results - rejected_results)
        response_data["result_count"] = total_results
        response_data["uploaded_count"] = uploaded_count
        response_data["processed_count"] = total_results
        response_data["succeeded_count"] = succeeded_results
        response_data["rejected_count"] = rejected_results

    # Phase 29: Status-specific enrichment
    if task["status"] == "FAILED":
        response_data["progress"] = float(task.get("progress") or 0.0)
        response_data["eta_seconds"] = 0
        # Sanitize error messages: strip internal stack traces
        raw_error = task.get("error_message", "Unknown error")
        response_data["retryable"] = True
        response_data["retry_hint"] = "retry_submit"
        response_data["next_action"] = "retry_upload"
        if "Traceback" in raw_error or "File " in raw_error:
            response_data["error_code"] = "INTERNAL_ERROR"
            response_data["error_message"] = "Internal processing error. Contact support."
        else:
            response_data["error_code"] = "TASK_FAILED"
            response_data["error_message"] = raw_error[:200]  # Truncate long errors
    
    elif task["status"] == "COMPLETED":
        response_data["progress"] = 1.0
        response_data["eta_seconds"] = 0
        if task.get("grading_status") == "REJECTED_UNREADABLE":
            response_data["error_code"] = "INPUT_REJECTED"
            response_data["error_message"] = task.get("error_message", "Input quality too low")
            response_data["retryable"] = True
            response_data["retry_hint"] = "resubmit_with_clearer_image"
            response_data["next_action"] = "retry_upload"
        else:
            response_data["next_action"] = "view_results"
        # Retrieve results associated with this task
        async with _open_connection(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM grading_results WHERE task_id = ?", (task_id,)) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    # Try to parse report_json back into a dict for cleaner API output
                    try:
                        item["report_json"] = json.loads(item["report_json"])
                    except:
                        pass
                    results.append(item)
                if results:
                    response_data["results"] = results
                else:
                    paper_task = await get_paper_task(db_path, task_id)
                    if paper_task:
                        question_rows = await list_paper_question_results(db_path, task_id)
                        for row in question_rows:
                            try:
                                row["report_json"] = _json_response_safe(json.loads(row["report_json"]))
                            except Exception:
                                pass
                            try:
                                row["page_indexes_json"] = json.loads(row["page_indexes_json"] or "[]")
                            except Exception:
                                pass
                        try:
                            paper_report = _json_response_safe(json.loads(paper_task["report_json"]))
                        except Exception:
                            paper_report = _json_response_safe(paper_task["report_json"])
                        paper_task_payload = dict(paper_task)
                        paper_task_payload["report_json"] = paper_report
                        paper_task_payload = _json_response_safe(paper_task_payload)
                        response_data["results"] = [
                            {
                                "paper_task": paper_task_payload,
                                "paper_report": paper_report,
                                "question_results": _json_response_safe(question_rows),
                            }
                        ]
    
    # Phase 32: Generate ETag from task state
    # ETag = hash(status + updated_at + error_message)
    state_string = (
        f"{task['status']}_{task.get('updated_at', '')}_{task.get('error_message', '')}_"
        f"{response_data.get('submitted_count', 0)}_{response_data.get('result_count', 0)}"
    )
    etag = hashlib.md5(state_string.encode()).hexdigest()
    
    # Check If-None-Match header (conditional request)
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match == etag:
        # Task state unchanged - return 304 Not Modified
        response.status_code = 304
        response.headers["ETag"] = etag
        response.headers["Last-Modified"] = task.get("updated_at", "")
        return Response(status_code=304)
    
    # Task state changed - return full payload with cache headers
    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = task.get("updated_at", "")
    response.headers["Cache-Control"] = "private, must-revalidate"
    
    return TaskStatusResponse(**response_data)


@router.get("/grade-batch/{task_id}", response_model=TaskStatusResponse)
@limiter.limit("30/minute")
async def get_batch_job_status_and_results(
    request: Request,
    response: Response,
    task_id: str,
    db_path: str = Depends(get_db_path),
):
    return await get_job_status_and_results(
        request=request,
        response=response,
        task_id=task_id,
        db_path=db_path,
    )


@router.get("/grade/paper/reports")
async def list_paper_reports(
    bundle_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    limit: int = Query(100, ge=1, le=200),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    from src.db.client import _open_connection, aiosqlite
    from src.db.dao._migrations import _ensure_paper_grading_tables

    selected_bundle_id = str(bundle_id or "").strip()
    if not selected_bundle_id and task_id:
        paper_task = await get_paper_task(db_path, task_id)
        if not paper_task:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    error_code="PAPER_TASK_NOT_FOUND",
                    message="Paper task not found",
                    retryable=False,
                    next_action="submit_new_task",
                ),
            )
        selected_bundle_id = str(paper_task.get("bundle_id") or "").strip()

    async with _open_connection(db_path) as db:
        await _ensure_paper_grading_tables(db)
        db.row_factory = aiosqlite.Row
        if not selected_bundle_id:
            async with db.execute(
                """
                SELECT bundle_id FROM paper_tasks
                WHERE bundle_id IS NOT NULL AND bundle_id != ''
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ) as cursor:
                latest = await cursor.fetchone()
                selected_bundle_id = str(latest["bundle_id"] if latest else "").strip()
        if not selected_bundle_id:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    error_code="PAPER_REPORTS_NOT_FOUND",
                    message="No paper reports found",
                    retryable=False,
                    next_action="submit_new_task",
                ),
            )

        where = ["pt.bundle_id = ?"]
        params: List[Any] = [selected_bundle_id]
        if settings.auth_enabled:
            where.append("t.teacher_id = ?")
            params.append(teacher.teacher_id)
        params.append(limit)
        async with db.execute(
            f"""
            SELECT
                pt.*,
                t.status AS task_status,
                t.grading_status,
                t.review_status,
                t.progress,
                t.created_at AS task_created_at,
                t.updated_at AS task_updated_at
            FROM paper_tasks pt
            JOIN tasks t ON t.task_id = pt.task_id
            WHERE {' AND '.join(where)}
            ORDER BY pt.student_id ASC, pt.updated_at ASC
            LIMIT ?
            """,
            tuple(params),
        ) as cursor:
            task_rows = [dict(row) for row in await cursor.fetchall()]

        raw_attempt_count = len(task_rows)
        latest_by_student: Dict[str, Dict[str, Any]] = {}
        latest_rank_by_student: Dict[str, tuple[int, str]] = {}
        for row in task_rows:
            base_student_id = _base_paper_student_id(row.get("student_id")) or str(row.get("task_id") or "")
            rank = (
                1 if str(row.get("task_status") or "") == "COMPLETED" else 0,
                str(row.get("task_updated_at") or row.get("updated_at") or ""),
            )
            if base_student_id not in latest_by_student or rank >= latest_rank_by_student[base_student_id]:
                row["source_student_id"] = row.get("student_id")
                row["student_id"] = base_student_id
                latest_by_student[base_student_id] = row
                latest_rank_by_student[base_student_id] = rank
        task_rows = sorted(latest_by_student.values(), key=lambda item: str(item.get("student_id") or ""))

        task_ids = [str(row["task_id"]) for row in task_rows]
        question_rows_by_task: Dict[str, List[Dict[str, Any]]] = {item: [] for item in task_ids}
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            async with db.execute(
                f"""
                SELECT * FROM paper_question_results
                WHERE task_id IN ({placeholders})
                ORDER BY task_id ASC, created_at ASC, id ASC
                """,
                tuple(task_ids),
            ) as cursor:
                for row in await cursor.fetchall():
                    item = dict(row)
                    try:
                        item["report_json"] = _json_response_safe(json.loads(item["report_json"]))
                    except Exception:
                        item["report_json"] = _json_response_safe(item.get("report_json"))
                    try:
                        item["page_indexes_json"] = json.loads(item.get("page_indexes_json") or "[]")
                    except Exception:
                        item["page_indexes_json"] = []
                    question_rows_by_task.setdefault(str(item["task_id"]), []).append(item)

    students: List[Dict[str, Any]] = []
    question_ids: List[str] = []
    for row in task_rows:
        try:
            paper_report = _json_response_safe(json.loads(row.get("report_json") or "{}"))
        except Exception:
            paper_report = _json_response_safe(row.get("report_json"))
        question_results = _json_response_safe(question_rows_by_task.get(str(row["task_id"]), []))
        if isinstance(paper_report, dict) and isinstance(paper_report.get("per_question"), dict):
            paper_report = _enrich_paper_report_evidence(paper_report)
            paper_report["input_images_by_question"] = _paper_report_input_images(
                str(row["task_id"]),
                paper_report,
            )
            paper_report.pop("input_file_refs_by_question", None)
            paper_report.pop("input_filenames_by_question", None)
            for question_id in paper_report["per_question"]:
                if str(question_id) not in question_ids:
                    question_ids.append(str(question_id))
        students.append(
            {
                "task_id": row["task_id"],
                "student_id": row.get("student_id"),
                "source_student_id": row.get("source_student_id"),
                "bundle_id": row.get("bundle_id"),
                "paper_id": row.get("paper_id"),
                "task_status": row.get("task_status"),
                "grading_status": row.get("grading_status"),
                "review_status": row.get("review_status"),
                "progress": row.get("progress"),
                "total_questions": row.get("total_questions"),
                "answered_questions": row.get("answered_questions"),
                "total_score_deduction": row.get("total_score_deduction"),
                "requires_human_review": bool(row.get("requires_human_review")),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "task_created_at": row.get("task_created_at"),
                "task_updated_at": row.get("task_updated_at"),
                "paper_report": paper_report,
                "question_results": question_results,
            }
        )

    completed_count = sum(1 for item in students if item.get("task_status") == "COMPLETED")
    review_count = sum(1 for item in students if item.get("requires_human_review"))
    total_deduction = sum(float(item.get("total_score_deduction") or 0.0) for item in students)
    return {
        "bundle_id": selected_bundle_id,
        "paper_id": students[0]["paper_id"] if students else None,
        "task_count": len(students),
        "raw_attempt_count": raw_attempt_count,
        "student_count": len(students),
        "completed_count": completed_count,
        "review_count": review_count,
        "average_deduction": (total_deduction / len(students)) if students else 0.0,
        "question_ids": question_ids,
        "students": students,
    }


@router.get("/grade/paper/inputs")
async def get_paper_input_asset(
    task_id: str = Query(...),
    question_id: str = Query(...),
    index: int = Query(0, ge=0),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    from src.db.client import _open_connection, aiosqlite

    where = ["pt.task_id = ?"]
    params: List[Any] = [task_id]
    if settings.auth_enabled:
        where.append("t.teacher_id = ?")
        params.append(teacher.teacher_id)

    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT pt.report_json
            FROM paper_tasks pt
            JOIN tasks t ON t.task_id = pt.task_id
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="paper task not found")
    try:
        paper_report = json.loads(row["report_json"] or "{}")
    except Exception:
        paper_report = {}
    refs_by_question = paper_report.get("input_file_refs_by_question")
    refs = refs_by_question.get(question_id) if isinstance(refs_by_question, dict) else None
    if not isinstance(refs, list) or index >= len(refs):
        raise HTTPException(status_code=404, detail="input asset not found")

    file_ref = str(refs[index] or "").strip()
    if not file_ref:
        raise HTTPException(status_code=404, detail="input asset not found")
    return _local_file_response_from_ref(file_ref)


@router.get("/tasks/{task_id}/stream")
async def stream_task_status(
    task_id: str,
    db_path: str = Depends(get_db_path)
):
    """
    Phase 32: Server-Sent Events (SSE) - Real-time task status push.
    
    Replaces polling with long-lived HTTP connection that receives status updates.
    Automatically closes when task reaches terminal state (COMPLETED/FAILED).
    
    Client Usage (JavaScript):
        const eventSource = new EventSource('/api/v1/tasks/{task_id}/stream');
        
        eventSource.addEventListener('status_update', (event) => {
            const data = JSON.parse(event.data);
            updateUI(data.status, data.progress);
        });
        
        eventSource.addEventListener('complete', (event) => {
            eventSource.close();
            fetchFullResults(task_id);
        });
    
    Benefits:
        - Eliminates polling overhead (30 req/min → 1 connection)
        - Sub-second status updates (vs 2s polling interval)
        - Standard SSE protocol (browser-native support)
    
    Returns:
        EventSourceResponse with text/event-stream content-type
    """
    # Verify task exists before opening SSE stream
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )
    
    return create_sse_response(db_path, task_id)


@router.get("/results", response_model=List[GradingResultItem])
async def get_all_results(
    task_id: Optional[str] = Query(default=None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db_path: str = Depends(get_db_path)
):
    """Paginated result retrieval."""
    if task_id:
        task = await get_task(db_path, task_id)
        if not task:
            raise HTTPException(
                status_code=404,
                detail=_error_detail(
                    error_code="TASK_NOT_FOUND",
                    message="Task not found",
                    retryable=False,
                    next_action="submit_new_task",
                ),
            )
        if task.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=409,
                detail=_error_detail(
                    error_code="TASK_NOT_COMPLETED",
                    message="Task is not completed yet",
                    retryable=True,
                    retry_hint="wait_and_poll_status",
                    next_action="wait_for_completion",
                ),
            )
    offset = (page - 1) * limit
    if task_id:
        from src.db.client import _open_connection, aiosqlite

        async with _open_connection(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, student_id, total_deduction, is_pass, report_json
                FROM grading_results
                WHERE task_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (task_id, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                results = [dict(r) for r in rows]
    else:
        results = await fetch_results(db_path, limit, offset)
    return [GradingResultItem(**r) for r in results]


@router.get("/results/{result_id}/inputs/{index}")
async def get_result_input_asset(
    result_id: int,
    index: int,
    db_path: str = Depends(get_db_path),
):
    from src.db.client import _open_connection, aiosqlite

    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT report_json FROM grading_results WHERE id = ?",
            (result_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="result not found")

    try:
        payload = json.loads(row["report_json"])
    except Exception:
        payload = {}

    file_refs = payload.get("input_file_refs") if isinstance(payload.get("input_file_refs"), list) else []
    if index < 0 or index >= len(file_refs):
        raise HTTPException(status_code=404, detail="input asset not found")

    file_ref = str(file_refs[index] or "").strip()
    return _local_file_response_from_ref(file_ref)


@router.get("/tasks/history", response_model=TaskHistoryResponse)
async def get_task_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(default=None, pattern="^(PENDING|PROCESSING|COMPLETED|FAILED)$"),
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    offset = (page - 1) * limit
    where_conditions: List[str] = []
    params: List[Any] = []
    if status:
        where_conditions.append("t.status = ?")
        params.append(status)
    if settings.auth_enabled:
        where_conditions.append("t.teacher_id = ?")
        params.append(teacher.teacher_id)
    where_clause = ("WHERE " + " AND ".join(where_conditions)) if where_conditions else ""
    params.extend([limit, offset])

    from src.db.client import _open_connection, aiosqlite
    from src.db.dao._migrations import _ensure_paper_grading_tables

    async with _open_connection(db_path) as db:
        await _ensure_paper_grading_tables(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT
                t.task_id,
                t.status,
                t.grading_status,
                t.review_status,
                t.rubric_id,
                t.submitted_count,
                t.progress,
                t.created_at,
                t.updated_at,
                COALESCE((SELECT COUNT(1) FROM grading_results gr WHERE gr.task_id = t.task_id), 0)
                + COALESCE((SELECT COUNT(1) FROM paper_question_results pqr WHERE pqr.task_id = t.task_id), 0)
                AS result_count
            FROM tasks t
            {where_clause}
            ORDER BY t.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        ) as cursor:
            rows = await cursor.fetchall()
            items = [TaskHistoryItem(**dict(r)) for r in rows]
    return TaskHistoryResponse(page=page, limit=limit, items=items)


@router.get("/grade/{task_id}/report", response_model=TaskReportResponse)
async def get_task_report(
    task_id: str,
    db_path: str = Depends(get_db_path),
):
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )
    if task.get("status") != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail=_error_detail(
                error_code="TASK_NOT_COMPLETED",
                message="Task is not completed yet",
                retryable=True,
                retry_hint="wait_and_poll_status",
                next_action="wait_for_completion",
            ),
        )

    from src.db.client import _open_connection, aiosqlite

    async with _open_connection(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, student_id, total_deduction, is_pass, report_json
            FROM grading_results
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            records = [dict(r) for r in rows]

    cards = [_to_report_card(record) for record in records]
    if not cards:
        cards = [_paper_question_row_to_report_card(record) for record in await list_paper_question_results(db_path, task_id)]
    return TaskReportResponse(
        task_id=task_id,
        task_status=str(task.get("status") or "UNKNOWN"),
        cards=cards,
    )


@router.get("/grade/{task_id}/insights", response_model=TaskInsightsResponse)
async def get_task_insights(
    task_id: str,
    db_path: str = Depends(get_db_path),
):
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
                retryable=False,
                next_action="submit_new_task",
            ),
        )

    rows = await fetch_results_by_task(db_path, task_id)
    cards = [_to_report_card(record) for record in rows]
    insights = _build_task_insights(cards)
    return TaskInsightsResponse(
        task_id=task_id,
        task_status=str(task.get("status") or "UNKNOWN"),
        error_type_counts=insights["error_type_counts"],
        review_bucket_counts=insights["review_bucket_counts"],
        hotspots=insights["hotspots"],
        lecture_suggestions=insights["lecture_suggestions"],
    )


# ---------------------------------------------------------------------------
# Task Cancellation
# ---------------------------------------------------------------------------

@router.post("/grade/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db_path: str = Depends(get_db_path),
    teacher: TeacherIdentity = Depends(get_current_teacher),
):
    """Cancel a PENDING or PROCESSING task.

    1. Revoke the Celery task (terminate if running).
    2. Remove any queued messages from Redis.
    3. Mark the task CANCELLED in DB.
    4. Publish CANCELLED event via Redis PubSub for SSE listeners.
    """
    task = await get_task(db_path, task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="TASK_NOT_FOUND",
                message="任务不存在",
                retryable=False,
                next_action="submit_new_task",
            ),
        )

    current_status = str(task.get("status") or "")
    if current_status not in {"PENDING", "PROCESSING"}:
        return {
            "task_id": task_id,
            "cancelled": False,
            "message": f"任务已处于终态 ({current_status})，无法取消",
            "previous_status": current_status,
        }

    # 1) Revoke Celery task (terminate=True sends SIGTERM to running worker)
    revoked = False
    revoke_error = None
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        revoked = True
    except Exception as exc:
        revoke_error = str(exc)[:200]
        logger.warning(
            "cancel_revoke_failed",
            extra={"extra_fields": {"task_id": task_id, "error": revoke_error}},
        )

    # 2) Remove from Redis queue (in case still queued)
    removed_count, queue_error = _remove_task_from_celery_queue(task_id)

    # 3) Mark CANCELLED in DB
    await update_task_status(
        db_path,
        task_id,
        "CANCELLED",
        error="教师主动取消任务",
        fallback_reason="TEACHER_CANCELLED",
    )

    # 4) Publish CANCELLED event for SSE
    try:
        await _publish_task_status(task_id, "CANCELLED", progress=1.0, eta_seconds=0)
    except Exception:
        pass  # best-effort

    logger.info(
        "task_cancelled",
        extra={
            "extra_fields": {
                "event": "task_cancelled",
                "task_id": task_id,
                "previous_status": current_status,
                "revoked": revoked,
                "removed_from_queue": removed_count,
            }
        },
    )

    return {
        "task_id": task_id,
        "cancelled": True,
        "previous_status": current_status,
        "revoked": revoked,
        "removed_from_queue": removed_count,
        "message": "任务已取消",
    }


# ---------------------------------------------------------------------------
# Redis Health Check (used by submit endpoints)
# ---------------------------------------------------------------------------

def _check_redis_health() -> tuple[bool, str]:
    """Quick Redis ping with instance identification for dual-Redis debugging."""
    target = f"{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
    try:
        import redis as _redis
        client = _redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        logger.debug(f"Redis health OK at {target}")
        return True, ""
    except Exception as exc:
        logger.warning(f"Redis health FAIL at {target}: {exc}")
        return False, str(exc)[:200]


