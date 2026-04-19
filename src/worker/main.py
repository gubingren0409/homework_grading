"""
Phase 33: Celery Worker - Distributed Event-Driven Architecture

This module decouples AI computation from the FastAPI gateway.
Workers consume tasks from Redis queue and execute GradingWorkflow asynchronously.

Phase 33 Enhancements:
- Redis Pub/Sub: Worker publishes status updates after DB writes
- Multi-node support: API nodes receive events regardless of physical location
- Event-driven SSE: Sub-100ms latency vs 1s database polling

Usage:
    # Linux/macOS:
    celery -A src.worker.main worker --loglevel=info --concurrency=4

    # Windows (compatibility mode):
    celery -A src.worker.main worker --loglevel=info --pool=solo --concurrency=1
"""
import asyncio
import json
import logging
import math
import os
import time
import uuid
from typing import List, Tuple, Dict, Any

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from src.core.config import settings
from src.core.exceptions import PerceptionShortCircuitError
from src.core.storage_adapter import storage
from src.db.client import insert_grading_results, update_task_progress, update_task_status, save_grading_result
from src.db.client import create_hygiene_interception_record
from src.db.client import upsert_task_runtime_telemetry
from src.db.client import get_recent_rubric_by_fingerprint, get_rubric, save_rubric, set_task_rubric_id
from src.db.client import touch_task_heartbeat
from src.db.client import get_task as _get_task_from_db
from src.orchestration.workflow import GradingWorkflow
from src.perception.factory import create_perception_engine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.core.trace_context import bind_context, reset_context, get_trace_id
from src.core.json_logging import configure_json_logging
from src.schemas.rubric_ir import TeacherRubric
from src.schemas.cognitive_ir import EvaluationReport
from src.skills.service import SkillService
from src.utils.file_parsers import process_multiple_files
from src.worker.helpers import (
    compute_effective_batch_concurrency,
    compute_source_fingerprint,
    derive_interception_node,
    project_batch_task_summary,
    project_statuses,
    should_emit_batch_progress,
)
from src.worker.pubsub import publish_status, route_to_dlq


logger = logging.getLogger(__name__)
configure_json_logging(level=logging.INFO)
_SKILL_SERVICE = SkillService(db_path=settings.sqlite_db_path)
_WORKER_TASK_LOOP: asyncio.AbstractEventLoop | None = None

_project_statuses = project_statuses
_project_batch_task_summary = project_batch_task_summary
_derive_interception_node = derive_interception_node
_compute_effective_batch_concurrency = compute_effective_batch_concurrency
_compute_source_fingerprint = compute_source_fingerprint
_should_emit_batch_progress = should_emit_batch_progress


def _get_worker_task_loop() -> asyncio.AbstractEventLoop:
    global _WORKER_TASK_LOOP
    if _WORKER_TASK_LOOP is None or _WORKER_TASK_LOOP.is_closed():
        _WORKER_TASK_LOOP = asyncio.new_event_loop()
    return _WORKER_TASK_LOOP


# Celery Application Initialization
app = Celery(
    "homework_grader_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# Celery 官方 FAQ 明确 Windows 非正式支持。
# 为避免 billiard/prefork 在 Windows 上出现 fast_trace_task 初始化异常，
# 强制切换为 solo 池（单进程，稳定优先）。
_is_windows = os.name == "nt"
_worker_conf = dict(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,  # Result TTL: 1 hour
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # Only ack after task completion (failure-tolerant)
    worker_prefetch_multiplier=1,  # Prevent task hoarding in workers
    broker_connection_retry_on_startup=True,  # Tolerate Redis startup delays
    task_reject_on_worker_lost=True,  # Send to DLQ if worker crashes
    task_default_max_retries=2,  # Global max retries
    task_always_eager=settings.celery_task_always_eager,
    task_store_eager_result=True,
    # Phase 10: Redis visibility timeout — unacked messages re-delivered after this time.
    # Prevents message loss when a worker acks then dies before completing.
    broker_transport_options={
        "visibility_timeout": settings.celery_visibility_timeout,
    },
)
if _is_windows:
    _worker_conf.update(
        worker_pool="solo",
        worker_concurrency=1,
    )

# Celery Configuration (Phase 28, Phase 32: DLQ)
app.conf.update(**_worker_conf)

# Phase 32: Dead Letter Queue Names
DLQ_QUEUE_NAME = "grading_tasks_dlq"
DLQ_EXCHANGE = "dlq"


@app.task(bind=True, name="src.worker.main.emit_trace_probe")
def emit_trace_probe(self, task_id: str) -> dict:
    """Phase 34 trace/log probe task for observability verification."""
    request_trace_id = (self.request.headers or {}).get("trace_id", "-")
    tokens = bind_context(trace_id=request_trace_id, task_id=task_id, component="worker")
    try:
        logger.info("worker_task_pulled")
        logger.info(
            "llm_request_outbound",
            extra={"extra_fields": {"component": "trace-probe", "model": "simulated"}}
        )
        logger.info(
            "task_status_persisted",
            extra={"extra_fields": {"status": "COMPLETED"}}
        )
        return {"status": "ok", "task_id": task_id}
    finally:
        reset_context(tokens)


def _build_workflow() -> GradingWorkflow:
    """
    Factory function: Instantiate GradingWorkflow with fresh engine instances.
    Each worker process maintains independent engine pools.
    """
    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    return GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
        skill_service=_SKILL_SERVICE,
    )


@app.task(bind=True, max_retries=2, default_retry_delay=10,
         soft_time_limit=settings.celery_task_soft_time_limit_seconds,
         time_limit=settings.celery_task_hard_time_limit_seconds)
def grade_homework_task(
    self,
    task_id: str,
    payload: Dict[str, Any],  # Phase 32: Storage adapter payload with file_refs
    db_path: str,
) -> dict:
    """
    Celery Task: Execute grading workflow in isolated worker process.

    Args:
        task_id: Business task UUID
        payload: Storage adapter payload with {"file_refs": ["file://..." or "s3://..."]}
        db_path: SQLite database path

    Returns:
        Status dict with task_id and completion state

    Retry Policy:
        - Max 2 retries on transient failures
        - 10s delay between retries
        - Permanent failure marked as FAILED in DB
    """
    # Phase 30: Explicit event loop creation/disposal (no nest-asyncio pollution)
    request_trace_id = (self.request.headers or {}).get("trace_id", "-")
    ctx_tokens = bind_context(
        trace_id=request_trace_id,
        task_id=task_id,
        component="worker",
    )
    task_loop = _get_worker_task_loop()
    asyncio.set_event_loop(task_loop)
    task_started_at = time.monotonic()
    eta_bootstrap_per_item_seconds = 28.0
    eta_bootstrap_overhead_seconds = 50.0
    payload_file_refs = payload.get("file_refs", []) if isinstance(payload, dict) else []
    total_input_count = max(1, len(payload_file_refs) if isinstance(payload_file_refs, list) else 1)

    def run_async(coro):
        return task_loop.run_until_complete(coro)

    def estimate_eta_seconds(*, completed_items: int = 0, total_items: int | None = None, floor_seconds: int = 5) -> int:
        safe_total = max(1, int(total_items or total_input_count))
        elapsed = max(0.0, time.monotonic() - task_started_at)
        if completed_items > 0:
            effective_elapsed = max(1.0, elapsed - eta_bootstrap_overhead_seconds)
            average_seconds = max(1.0, effective_elapsed / completed_items)
            remaining_items = max(0, safe_total - completed_items)
            if remaining_items == 0:
                return 0
            observed_remaining = average_seconds * remaining_items
            bootstrap_remaining = max(
                0.0,
                eta_bootstrap_overhead_seconds + eta_bootstrap_per_item_seconds * safe_total - elapsed,
            )
            confidence = min(1.0, completed_items / max(2.0, safe_total / 2.0))
            blended_remaining = bootstrap_remaining * (1.0 - confidence) + observed_remaining * confidence
            return max(floor_seconds, int(math.ceil(blended_remaining)))
        expected_total = eta_bootstrap_overhead_seconds + eta_bootstrap_per_item_seconds * safe_total
        return max(floor_seconds, int(math.ceil(max(0.0, expected_total - elapsed))))
    
    try:
        logger.info("worker_task_pulled")

        # Pre-check: if task was cancelled before worker picked it up, skip.
        try:
            pre_task = run_async(_get_task_from_db(db_path, task_id))
            if pre_task and str(pre_task.get("status", "")) == "CANCELLED":
                logger.info("worker_task_already_cancelled", extra={"extra_fields": {"task_id": task_id}})
                return {"task_id": task_id, "status": "CANCELLED", "reason": "cancelled_before_start"}
        except RuntimeError:
            pass  # Eager mode / nested event loop — skip pre-check

        # Step 1: Mark task as processing
        run_async(update_task_status(db_path, task_id, "PROCESSING"))
        run_async(touch_task_heartbeat(db_path, task_id))
        run_async(update_task_progress(db_path, task_id, progress=0.02, eta_seconds=estimate_eta_seconds(floor_seconds=30)))
        logger.info("task_status_persisted", extra={"extra_fields": {"status": "PROCESSING"}})
        # Phase 33: Publish status update to Redis Pub/Sub
        run_async(_publish_status(task_id, "PROCESSING", progress=0.02, eta_seconds=estimate_eta_seconds(floor_seconds=30)))
        logger.info(f"[Worker] Task {task_id} started processing")

        # Step 2: Retrieve files from storage backend (Phase 32)
        file_refs = payload.get("file_refs", [])
        reconstructed_files = storage.retrieve_files(file_refs)
        run_async(update_task_progress(db_path, task_id, progress=0.05, eta_seconds=estimate_eta_seconds(floor_seconds=25)))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.05, eta_seconds=estimate_eta_seconds(floor_seconds=25)))

        # Step 3: Initialize workflow (worker-local instance)
        workflow = _build_workflow()
        run_async(update_task_progress(db_path, task_id, progress=0.07, eta_seconds=estimate_eta_seconds(floor_seconds=20)))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.07, eta_seconds=estimate_eta_seconds(floor_seconds=20)))

        # Step 4: Execute core grading pipeline (with optional rubric binding)
        rubric_obj = None
        rubric_json = payload.get("rubric_json")
        if rubric_json is not None:
            rubric_obj = TeacherRubric.model_validate(rubric_json)
        reference_file_refs_raw = payload.get("reference_file_refs")
        reference_file_refs = (
            [str(ref) for ref in reference_file_refs_raw]
            if isinstance(reference_file_refs_raw, list)
            else []
        )
        mode = str(payload.get("mode") or "single_submission").strip().lower()
        if mode == "batch_single_page":
            eta_bootstrap_per_item_seconds = 22.0
            eta_bootstrap_overhead_seconds = 45.0 + (30.0 if reference_file_refs else 0.0)
        else:
            eta_bootstrap_per_item_seconds = 35.0
            eta_bootstrap_overhead_seconds = 35.0 + (15.0 if reference_file_refs else 0.0)
        student_id_override = str(payload.get("student_id") or "").strip()
        batch_student_ids_raw = payload.get("student_ids")
        auto_rubric_source_files: List[Tuple[bytes, str]] = []

        # Auto-rubric path: rubric generation moves into worker task when reference files are provided.
        if rubric_obj is None and reference_file_refs:
            run_async(update_task_progress(db_path, task_id, progress=0.08, eta_seconds=estimate_eta_seconds(floor_seconds=20)))
            run_async(_publish_status(task_id, "PROCESSING", progress=0.08, eta_seconds=estimate_eta_seconds(floor_seconds=20)))

            reference_files = storage.retrieve_files(reference_file_refs)
            if not reference_files:
                raise ValueError("reference_file_refs provided but no valid files were retrieved")

            source_fingerprint = _compute_source_fingerprint(reference_files)
            cached = run_async(
                get_recent_rubric_by_fingerprint(
                    db_path,
                    source_fingerprint=source_fingerprint,
                    within_seconds=settings.rubric_dedupe_window_seconds,
                )
            )
            if cached:
                cached_row = run_async(get_rubric(db_path, str(cached["rubric_id"])))
                if not cached_row:
                    raise ValueError(f"Cached rubric {cached['rubric_id']} not found")
                cached_rubric_raw = cached_row.get("rubric_json")
                cached_rubric_obj = (
                    json.loads(cached_rubric_raw)
                    if isinstance(cached_rubric_raw, str)
                    else cached_rubric_raw
                )
                rubric_obj = TeacherRubric.model_validate(cached_rubric_obj)
                rubric_json = rubric_obj.model_dump()
                run_async(set_task_rubric_id(db_path, task_id, str(cached["rubric_id"])))
            else:
                auto_rubric_source_files = reference_files

        # Step 5: Execute + persist result(s)
        run_async(update_task_progress(db_path, task_id, progress=0.10, eta_seconds=estimate_eta_seconds(floor_seconds=15)))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.10, eta_seconds=estimate_eta_seconds(floor_seconds=15)))

        report = None
        reports: List[Any] = []
        runtime_telemetry = None
        cognitive_agent = getattr(workflow, "_cognitive_agent", None)
        validation_service = SkillService(db_path=db_path)

        if mode == "batch_single_page":
            if not reconstructed_files:
                raise ValueError("batch_single_page mode requires at least one file")
            batch_student_ids = (
                [str(x) for x in batch_student_ids_raw]
                if isinstance(batch_student_ids_raw, list)
                else []
            )
            if batch_student_ids and len(batch_student_ids) != len(reconstructed_files):
                raise ValueError(
                    f"batch student_ids length mismatch: expected={len(reconstructed_files)} got={len(batch_student_ids)}"
                )
            if not batch_student_ids:
                batch_student_ids = []
                for idx, (_, filename) in enumerate(reconstructed_files, start=1):
                    stem, _ = os.path.splitext(filename)
                    normalized = (stem or f"student_{idx}").strip()
                    batch_student_ids.append(normalized or f"student_{idx}")
            preprocessed_student_images: List[bytes | None] = [None] * len(reconstructed_files)

            # Decouple rubric generation and answer preprocessing:
            # run both in parallel when worker needs to auto-generate rubric.
            if rubric_obj is None and auto_rubric_source_files:
                async def _preprocess_batch_answers() -> List[bytes | None]:
                    sem = asyncio.Semaphore(
                        _compute_effective_batch_concurrency(
                            len(reconstructed_files),
                            int(settings.file_preprocess_concurrency),
                        )
                    )

                    async def _prep_one(file_bytes: bytes, filename: str) -> List[bytes]:
                        async with sem:
                            return await process_multiple_files([(file_bytes, filename)])

                    prepared = await asyncio.gather(
                        *[_prep_one(file_bytes, filename) for file_bytes, filename in reconstructed_files]
                    )
                    normalized: List[bytes | None] = []
                    for item in prepared:
                        normalized.append(item[0] if item else None)
                    return normalized

                async def _generate_rubric_and_preprocess() -> Tuple[TeacherRubric, List[bytes | None]]:
                    rubric_task = asyncio.create_task(
                        workflow.generate_rubric_pipeline(auto_rubric_source_files)
                    )
                    preprocess_task = asyncio.create_task(_preprocess_batch_answers())
                    generated_rubric, prepared_answers = await asyncio.gather(rubric_task, preprocess_task)
                    return generated_rubric, prepared_answers

                generated_rubric, preprocessed_student_images = run_async(_generate_rubric_and_preprocess())
                generated_rubric_id = str(uuid.uuid4())
                run_async(
                    save_rubric(
                        db_path,
                        rubric_id=generated_rubric_id,
                        question_id=generated_rubric.question_id,
                        rubric_json=generated_rubric.model_dump(),
                        source_fingerprint=_compute_source_fingerprint(auto_rubric_source_files),
                    )
                )
                run_async(set_task_rubric_id(db_path, task_id, generated_rubric_id))
                rubric_obj = generated_rubric
                rubric_json = generated_rubric.model_dump()

            batch_concurrency = _compute_effective_batch_concurrency(
                len(reconstructed_files),
                int(settings.batch_internal_concurrency),
            )
            batch_sem = asyncio.Semaphore(batch_concurrency)

            async def _process_one_batch_item(
                item_idx: int,
                file_bytes: bytes,
                filename: str,
                one_student_id: str,
                preprocessed_image_bytes: bytes | None,
            ) -> Tuple[int, Any, Dict[str, Any]]:
                async with batch_sem:
                    single_submission = [(file_bytes, filename)]
                    perception_snapshot = None
                    cognitive_snapshot = None
                    try:
                        if preprocessed_image_bytes is not None:
                            snapshot_result = await workflow.run_pipeline_with_preprocessed_images(
                                [preprocessed_image_bytes],
                                rubric=rubric_obj,
                            )
                        else:
                            snapshot_result = await workflow.run_pipeline_with_snapshots(single_submission, rubric=rubric_obj)  # type: ignore[attr-defined]
                        if not isinstance(snapshot_result, tuple) or len(snapshot_result) != 3:
                            raise TypeError("run_pipeline_with_snapshots must return (report, perception, cognitive)")
                        one_report, perception_snapshot, cognitive_snapshot = snapshot_result
                    except PerceptionShortCircuitError as per_exc:
                        one_report = EvaluationReport(
                            status="REJECTED_UNREADABLE",
                            is_fully_correct=False,
                            total_score_deduction=0.0,
                            step_evaluations=[],
                            overall_feedback=f"Perception short-circuit: {per_exc.readability_status}",
                            system_confidence=0.0,
                            requires_human_review=True,
                        )
                        perception_snapshot = {
                            "readability_status": per_exc.readability_status,
                            "elements": [],
                            "global_confidence": 0.0,
                            "is_blank": False,
                            "trigger_short_circuit": True,
                        }
                        cognitive_snapshot = one_report.model_dump()
                    except (AttributeError, TypeError):
                        if preprocessed_image_bytes is not None:
                            one_report, _, _ = await workflow.run_pipeline_with_preprocessed_images(
                                [preprocessed_image_bytes],
                                rubric=rubric_obj,
                            )
                        else:
                            one_report = await workflow.run_pipeline(single_submission, rubric=rubric_obj)
                    report_payload: Dict[str, Any] = {
                        "evaluation_report": (
                            one_report.model_dump()
                            if hasattr(one_report, "model_dump")
                            else one_report
                        ),
                    }
                    if perception_snapshot is not None:
                        report_payload["perception_output"] = perception_snapshot
                        report_payload["perception_ir_snapshot"] = perception_snapshot
                    if cognitive_snapshot is not None:
                        report_payload["cognitive_ir_snapshot"] = cognitive_snapshot
                    raw_ref = file_refs[item_idx] if item_idx < len(file_refs) else None
                    source_name = reconstructed_files[item_idx][1] if item_idx < len(reconstructed_files) else None
                    if raw_ref:
                        report_payload["input_file_refs"] = [raw_ref]
                    if source_name:
                        report_payload["input_filenames"] = [source_name]

                    grading_status = str(getattr(one_report, "status", "SCORED"))
                    if grading_status == "REJECTED_UNREADABLE":
                        raw_ref = file_refs[item_idx] if item_idx < len(file_refs) else None
                        await create_hygiene_interception_record(
                            db_path,
                            trace_id=get_trace_id(),
                            task_id=task_id,
                            interception_node=_derive_interception_node(one_report),
                            raw_image_path=raw_ref,
                            action="manual_review",
                        )

                    try:
                        validation_outcome = await validation_service.run_validation(
                            task_id=task_id,
                            student_id=one_student_id,
                            question_id=None,
                            perception_payload=perception_snapshot or {},
                            evaluation_payload=one_report.model_dump() if hasattr(one_report, "model_dump") else {},
                            rubric_payload=rubric_json if isinstance(rubric_json, dict) else None,
                        )
                        if validation_outcome.applied and validation_outcome.result is not None:
                            logger.info(
                                "external_validation_recorded",
                                extra={
                                    "extra_fields": {
                                        "task_id": task_id,
                                        "student_id": one_student_id,
                                        "checker": validation_outcome.result.checker,
                                        "status": validation_outcome.result.status,
                                        "confidence": validation_outcome.result.confidence,
                                    }
                                },
                            )
                    except Exception as skill_exc:
                        logger.warning(f"external validation skill failed: {skill_exc}")
                    return item_idx, one_report, {
                        "task_id": task_id,
                        "student_id": one_student_id,
                        "question_id": None,
                        "total_deduction": float(getattr(one_report, "total_score_deduction")),
                        "is_pass": bool(getattr(one_report, "is_fully_correct")),
                        "report_json": report_payload,
                    }

            batch_jobs = [
                _process_one_batch_item(
                    item_idx,
                    file_bytes,
                    filename,
                    batch_student_ids[item_idx],
                    preprocessed_student_images[item_idx],
                )
                for item_idx, (file_bytes, filename) in enumerate(reconstructed_files)
            ]

            async def _run_batch_jobs_with_progress() -> List[Any]:
                processed_reports: List[Any] = [None] * len(batch_jobs)
                total = len(batch_jobs)
                completed_count = 0
                for job in asyncio.as_completed(batch_jobs):
                    # Check for cancellation between items (best-effort)
                    if completed_count > 0 and completed_count % 3 == 0:
                        try:
                            _check = await _get_task_from_db(db_path, task_id)
                            if _check and str(_check.get("status", "")) == "CANCELLED":
                                logger.info("worker_batch_cancelled_mid_flight",
                                            extra={"extra_fields": {"task_id": task_id, "completed": completed_count, "total": total}})
                                break
                        except Exception:
                            pass
                    item_idx, one_report, record = await job
                    await insert_grading_results(db_path, records=[record], task_id=task_id)
                    processed_reports[item_idx] = one_report
                    completed_count += 1
                    progress = 0.10 + 0.85 * (completed_count / total)
                    eta_seconds = estimate_eta_seconds(completed_items=completed_count, total_items=total, floor_seconds=3)
                    await update_task_progress(db_path, task_id, progress=progress, eta_seconds=eta_seconds)
                    await touch_task_heartbeat(db_path, task_id)
                    await _publish_status(task_id, "PROCESSING", progress=progress, eta_seconds=eta_seconds)
                return [report for report in processed_reports if report is not None]

            reports = run_async(_run_batch_jobs_with_progress())

            if cognitive_agent is not None:
                telemetry_fn = getattr(cognitive_agent, "get_last_runtime_telemetry", None)
                if callable(telemetry_fn):
                    runtime_telemetry = telemetry_fn()

            grading_status, review_status = _project_batch_task_summary(reports)
            pipeline_status = "COMPLETED"
        else:
            perception_snapshot = None
            cognitive_snapshot = None
            try:
                snapshot_result = run_async(
                    workflow.run_pipeline_with_snapshots(reconstructed_files, rubric=rubric_obj)  # type: ignore[attr-defined]
                )
                if not isinstance(snapshot_result, tuple) or len(snapshot_result) != 3:
                    raise TypeError("run_pipeline_with_snapshots must return (report, perception, cognitive)")
                report, perception_snapshot, cognitive_snapshot = snapshot_result
            except (AttributeError, TypeError):
                report = run_async(workflow.run_pipeline(reconstructed_files, rubric=rubric_obj))

            student_id = (
                student_id_override
                or (reconstructed_files[0][1] if reconstructed_files else task_id)
            )
            run_async(
                save_grading_result(
                    db_path,
                    task_id,
                    student_id,
                    report,
                    perception_output=perception_snapshot,
                    cognitive_output=cognitive_snapshot,
                    report_payload_extras={
                        "input_file_refs": list(file_refs),
                        "input_filenames": [filename for _, filename in reconstructed_files],
                    },
                )
            )
            pipeline_status, review_status = _project_statuses(report)
            grading_status = str(getattr(report, "status", "SCORED"))
            if grading_status == "REJECTED_UNREADABLE":
                first_raw_ref = file_refs[0] if file_refs else None
                run_async(
                    create_hygiene_interception_record(
                        db_path,
                        trace_id=get_trace_id(),
                        task_id=task_id,
                        interception_node=_derive_interception_node(report),
                        raw_image_path=first_raw_ref,
                        action="manual_review",
                    )
                )

            try:
                validation_outcome = run_async(
                    validation_service.run_validation(
                        task_id=task_id,
                        student_id=student_id,
                        question_id=None,
                        perception_payload=perception_snapshot or {},
                        evaluation_payload=report.model_dump() if hasattr(report, "model_dump") else {},
                        rubric_payload=rubric_json if isinstance(rubric_json, dict) else None,
                    )
                )
                if validation_outcome.applied and validation_outcome.result is not None:
                    logger.info(
                        "external_validation_recorded",
                        extra={
                            "extra_fields": {
                                "task_id": task_id,
                                "checker": validation_outcome.result.checker,
                                "status": validation_outcome.result.status,
                                "confidence": validation_outcome.result.confidence,
                            }
                        },
                    )
            except Exception as skill_exc:
                logger.warning(f"external validation skill failed: {skill_exc}")

            if cognitive_agent is not None:
                telemetry_fn = getattr(cognitive_agent, "get_last_runtime_telemetry", None)
                if callable(telemetry_fn):
                    runtime_telemetry = telemetry_fn()

        if isinstance(runtime_telemetry, dict):
            run_async(
                upsert_task_runtime_telemetry(
                    db_path,
                    task_id=task_id,
                    trace_id=get_trace_id(),
                    requested_model=str(runtime_telemetry.get("requested_model") or settings.deepseek_model_name),
                    model_used=str(runtime_telemetry.get("model_used") or settings.deepseek_model_name),
                    route_reason=str(runtime_telemetry.get("route_reason") or "default"),
                    fallback_used=bool(runtime_telemetry.get("fallback_used", False)),
                    fallback_reason=(
                        str(runtime_telemetry.get("fallback_reason"))
                        if runtime_telemetry.get("fallback_reason") is not None
                        else None
                    ),
                    prompt_key=str(runtime_telemetry.get("prompt_key") or "deepseek.cognitive.evaluate"),
                    prompt_asset_version=str(runtime_telemetry.get("prompt_asset_version") or "unknown"),
                    prompt_variant_id=str(runtime_telemetry.get("prompt_variant_id") or "unknown"),
                    prompt_cache_level=str(runtime_telemetry.get("prompt_cache_level") or "SOURCE"),
                    prompt_token_estimate=int(runtime_telemetry.get("prompt_token_estimate") or 0),
                    succeeded=bool(runtime_telemetry.get("succeeded", True)),
                )
            )

        run_async(update_task_progress(db_path, task_id, progress=0.98, eta_seconds=estimate_eta_seconds(completed_items=total_input_count, total_items=total_input_count, floor_seconds=0)))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.98, eta_seconds=estimate_eta_seconds(completed_items=total_input_count, total_items=total_input_count, floor_seconds=0)))
        run_async(
            update_task_status(
                db_path,
                task_id,
                pipeline_status,
                grading_status=grading_status,
                review_status=review_status,
            )
        )
        logger.info(
            "task_status_persisted",
            extra={"extra_fields": {"status": pipeline_status, "grading_status": grading_status}},
        )
        # Phase 33: Publish completion event to Redis Pub/Sub
        run_async(
            _publish_status(
                task_id,
                pipeline_status,
                grading_status=grading_status,
                progress=1.0,
                eta_seconds=0,
                message="Grading completed successfully",
            )
        )

        logger.info(f"[Worker] Task {task_id} completed successfully")
        return {"status": "success", "task_id": task_id}

    except PerceptionShortCircuitError as e:
        # Defensive rejection (HEAVILY_ALTERED, UNREADABLE, blank detection)
        logger.warning(f"[Worker] Task {task_id} rejected by perception layer: {e}")
        first_raw_ref = payload.get("file_refs", [None])[0] if isinstance(payload, dict) else None
        node = "unreadable" if str(e.readability_status).upper() == "UNREADABLE" else "short_circuit"
        run_async(
            create_hygiene_interception_record(
                db_path,
                trace_id=get_trace_id(),
                task_id=task_id,
                interception_node=node,
                raw_image_path=first_raw_ref,
                action="manual_review",
            )
        )
        # P8.5-01: 单份模式拒判也写一条占位 grading_result，使 result_count 与
        # submitted_count 在拒判路径下也保持对齐，避免前端永远显示 0/1。
        try:
            placeholder_student_id = str(payload.get("student_id") or "").strip() or task_id
            placeholder_payload: Dict[str, Any] = {
                "evaluation_report": {
                    "status": "REJECTED_UNREADABLE",
                    "is_fully_correct": False,
                    "total_score_deduction": 0.0,
                    "requires_human_review": True,
                    "rejection_reason": str(e),
                    "readability_status": str(e.readability_status),
                },
                "perception_output": {
                    "readability_status": str(e.readability_status),
                    "trigger_short_circuit": True,
                },
            }
            input_refs = list(payload.get("file_refs") or []) if isinstance(payload, dict) else []
            if input_refs:
                placeholder_payload["input_file_refs"] = input_refs

            class _PlaceholderReport:
                status = "REJECTED_UNREADABLE"
                total_score_deduction = 0.0
                is_fully_correct = False

                def model_dump(self):
                    return placeholder_payload["evaluation_report"]

            run_async(
                save_grading_result(
                    db_path,
                    task_id,
                    placeholder_student_id,
                    _PlaceholderReport(),
                    perception_output=placeholder_payload["perception_output"],
                    report_payload_extras={
                        k: v for k, v in placeholder_payload.items()
                        if k not in ("evaluation_report", "perception_output")
                    },
                )
            )
        except Exception as placeholder_exc:
            logger.warning(
                "rejection_placeholder_persist_failed",
                extra={"extra_fields": {"task_id": task_id, "error": str(placeholder_exc)}},
            )
        run_async(
            update_task_status(
                db_path,
                task_id,
                "COMPLETED",
                error=f"Perception short-circuit: {e.readability_status}",
                grading_status="REJECTED_UNREADABLE",
                review_status="PENDING_REVIEW",
                fallback_reason=f"PERCEPTION_SHORT_CIRCUIT:{e.readability_status}",
            )
        )
        run_async(update_task_progress(db_path, task_id, progress=1.0, eta_seconds=0))
        logger.info(
            "task_status_persisted",
            extra={"extra_fields": {"status": "COMPLETED", "grading_status": "REJECTED_UNREADABLE"}},
        )
        # Phase 33: Publish rejection event to Redis Pub/Sub
        run_async(
            _publish_status(
                task_id,
                "COMPLETED",
                grading_status="REJECTED_UNREADABLE",
                progress=1.0,
                eta_seconds=0,
                error=str(e),
            )
        )
        return {"status": "rejected", "reason": str(e)}

    except SoftTimeLimitExceeded:
        timeout_msg = f"Task {task_id} exceeded soft time limit (900s). Likely stuck on LLM API."
        logger.error(f"[Worker] {timeout_msg}")
        run_async(update_task_status(db_path, task_id, "FAILED", error=timeout_msg))
        run_async(update_task_progress(db_path, task_id, progress=1.0, eta_seconds=0))
        run_async(_publish_status(task_id, "FAILED", error=timeout_msg, progress=1.0, eta_seconds=0))
        storage.cleanup_task(task_id)
        _route_to_dlq(task_id, payload, db_path, timeout_msg)
        return {"status": "failed", "error": timeout_msg}
    except Exception as e:
        # Transient failure: Rich error logging for faster post-mortem
        import traceback as _tb
        _exc_type = type(e).__name__
        _error_summary = f"{_exc_type}: {str(e)[:300]}"
        logger.error(
            "task_execution_failed",
            extra={"extra_fields": {
                "task_id": task_id,
                "attempt": self.request.retries + 1,
                "max_retries": self.max_retries + 1,
                "exception_type": _exc_type,
                "exception_message": str(e)[:500],
            }},
            exc_info=True,
        )
        run_async(update_task_status(db_path, task_id, "FAILED", error=_error_summary))
        run_async(update_task_progress(db_path, task_id, progress=1.0, eta_seconds=0))
        logger.info("task_status_persisted", extra={"extra_fields": {"status": "FAILED"}})
        # Phase 33: Publish failure event to Redis Pub/Sub
        run_async(_publish_status(task_id, "FAILED", error=_error_summary, progress=1.0, eta_seconds=0))

        # Cleanup on permanent failure (after max retries)
        if self.request.retries >= self.max_retries:
            storage.cleanup_task(task_id)
            
            # Phase 32: Route to Dead Letter Queue for audit
            _route_to_dlq(task_id, payload, db_path, _error_summary)
            
            logger.critical(f"[Worker] Task {task_id} permanently failed and routed to DLQ")
            return {"status": "failed", "error": _error_summary}

        # Retry if attempts remain
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        # Permanent failure after max retries
        logger.critical(f"[Worker] Task {task_id} permanently failed after {self.max_retries} retries")
        return {"status": "failed", "error": _error_summary}
    finally:
        reset_context(ctx_tokens)



async def _publish_status(task_id: str, status: str, **kwargs) -> None:
    await publish_status(task_id, status, **kwargs)


def _route_to_dlq(task_id: str, payload: Dict[str, Any], db_path: str, error: str) -> None:
    route_to_dlq(
        dlq_queue_name=DLQ_QUEUE_NAME,
        task_id=task_id,
        payload=payload,
        db_path=db_path,
        error=error,
    )

