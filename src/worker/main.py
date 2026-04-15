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
import contextvars
import logging
import os
import threading
import time
from typing import List, Tuple, Dict, Any

from celery import Celery

from src.core.config import settings
from src.core.exceptions import PerceptionShortCircuitError
from src.core.storage_adapter import storage
from src.db.client import update_task_progress, update_task_status, save_grading_result
from src.db.client import create_hygiene_interception_record
from src.db.client import upsert_task_runtime_telemetry
from src.orchestration.workflow import GradingWorkflow
from src.perception.factory import create_perception_engine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.core.trace_context import bind_context, reset_context, get_trace_id
from src.core.json_logging import configure_json_logging
from src.schemas.rubric_ir import TeacherRubric
from src.schemas.cognitive_ir import EvaluationReport
from src.skills.service import SkillService


logger = logging.getLogger(__name__)
configure_json_logging(level=logging.INFO)
_SKILL_SERVICE = SkillService(db_path=settings.sqlite_db_path)

# Pipeline status (execution) and grading status (business outcome) are projected separately.
def _project_statuses(report: Any) -> tuple[str, str]:
    grading_status = str(getattr(report, "status", "SCORED"))
    if grading_status == "REJECTED_UNREADABLE":
        return "COMPLETED", "PENDING_REVIEW"
    requires_review = bool(getattr(report, "requires_human_review", False))
    return "COMPLETED", ("PENDING_REVIEW" if requires_review else "NOT_REQUIRED")


def _project_batch_task_summary(reports: List[Any]) -> tuple[str, str]:
    """
    Summarize task-level grading/review status for batch-single-page mode.
    """
    if any(str(getattr(r, "status", "SCORED")) == "REJECTED_UNREADABLE" for r in reports):
        return "REJECTED_UNREADABLE", "PENDING_REVIEW"
    if any(bool(getattr(r, "requires_human_review", False)) for r in reports):
        return "SCORED", "PENDING_REVIEW"
    return "SCORED", "NOT_REQUIRED"


def _derive_interception_node(report: Any) -> str:
    """
    Infer hygiene interception node for rejected unreadable outputs.
    """
    feedback = str(getattr(report, "overall_feedback", "") or "")
    if "空白卷" in feedback or "未作答" in feedback:
        return "blank"
    return "short_circuit"


def _compute_effective_batch_concurrency(total_items: int, configured_concurrency: int) -> int:
    """
    Clamp in-task batch concurrency by item count to avoid creating redundant waiters.
    """
    if total_items <= 0:
        return 1
    return max(1, min(total_items, configured_concurrency))


def _should_emit_batch_progress(
    *,
    completed_count: int,
    total_count: int,
    last_emitted_count: int,
    last_emit_ts: float,
    now_ts: float,
) -> bool:
    """
    Throttle high-frequency progress writes/publishes for large batches.
    Always emit final completion tick.
    """
    if completed_count >= total_count:
        return True
    step = max(1, int(settings.batch_progress_update_step))
    if completed_count - last_emitted_count >= step:
        return True
    min_interval = float(settings.batch_progress_min_interval_seconds)
    return (now_ts - last_emit_ts) >= min_interval


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


@app.task(bind=True, max_retries=2, default_retry_delay=10)
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

    def run_async(coro):
        """
        Standard async bridge for Celery sync context.
        Reuses a process-local event loop in worker sync context to avoid
        closing loop-bound async clients between invocations.
        In eager mode (task executed inside an active event loop), run in a
        dedicated thread to avoid "Cannot run the event loop while another loop
        is running".
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)

        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Exception] = {}
        parent_ctx = contextvars.copy_context()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Preserve trace/task contextvars when eager mode forces a thread hop.
                result_holder["result"] = parent_ctx.run(loop.run_until_complete, coro)
            except Exception as exc:
                error_holder["error"] = exc
            finally:
                loop.close()

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join()
        if "error" in error_holder:
            raise error_holder["error"]
        return result_holder.get("result")
    
    try:
        logger.info("worker_task_pulled")
        # Step 1: Mark task as processing
        run_async(update_task_status(db_path, task_id, "PROCESSING"))
        run_async(update_task_progress(db_path, task_id, progress=0.1, eta_seconds=60))
        logger.info("task_status_persisted", extra={"extra_fields": {"status": "PROCESSING"}})
        # Phase 33: Publish status update to Redis Pub/Sub
        run_async(_publish_status(task_id, "PROCESSING", progress=0.1, eta_seconds=60))
        logger.info(f"[Worker] Task {task_id} started processing")

        # Step 2: Retrieve files from storage backend (Phase 32)
        file_refs = payload.get("file_refs", [])
        reconstructed_files = storage.retrieve_files(file_refs)
        run_async(update_task_progress(db_path, task_id, progress=0.3, eta_seconds=40))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.3, eta_seconds=40))

        # Step 3: Initialize workflow (worker-local instance)
        workflow = _build_workflow()
        run_async(update_task_progress(db_path, task_id, progress=0.5, eta_seconds=30))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.5, eta_seconds=30))

        # Step 4: Execute core grading pipeline (with optional rubric binding)
        rubric_obj = None
        rubric_json = payload.get("rubric_json")
        if rubric_json is not None:
            rubric_obj = TeacherRubric.model_validate(rubric_json)
        mode = str(payload.get("mode") or "single_submission").strip().lower()
        student_id_override = str(payload.get("student_id") or "").strip()
        batch_student_ids_raw = payload.get("student_ids")

        # Step 5: Execute + persist result(s)
        run_async(update_task_progress(db_path, task_id, progress=0.55, eta_seconds=25))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.55, eta_seconds=25))

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
            ) -> Tuple[int, Any, Any, Any, str]:
                async with batch_sem:
                    single_submission = [(file_bytes, filename)]
                    perception_snapshot = None
                    cognitive_snapshot = None
                    try:
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
                        one_report = await workflow.run_pipeline(single_submission, rubric=rubric_obj)
                    return item_idx, one_report, perception_snapshot, cognitive_snapshot, one_student_id

            batch_jobs = [
                _process_one_batch_item(idx, file_bytes, filename, batch_student_ids[idx])
                for idx, (file_bytes, filename) in enumerate(reconstructed_files)
            ]

            async def _run_batch_jobs_with_progress() -> List[Tuple[int, Any, Any, Any, str]]:
                completed_results: List[Tuple[int, Any, Any, Any, str]] = []
                total = len(batch_jobs)
                completed_count = 0
                last_emitted_count = 0
                last_emit_ts = 0.0
                for job in asyncio.as_completed(batch_jobs):
                    completed_results.append(await job)
                    completed_count += 1
                    now_ts = time.monotonic()
                    if _should_emit_batch_progress(
                        completed_count=completed_count,
                        total_count=total,
                        last_emitted_count=last_emitted_count,
                        last_emit_ts=last_emit_ts,
                        now_ts=now_ts,
                    ):
                        progress = 0.55 + 0.25 * (completed_count / total)
                        await update_task_progress(db_path, task_id, progress=progress, eta_seconds=15)
                        await _publish_status(task_id, "PROCESSING", progress=progress, eta_seconds=15)
                        last_emitted_count = completed_count
                        last_emit_ts = now_ts
                return completed_results

            batch_results = run_async(_run_batch_jobs_with_progress())
            ordered_batch_results = sorted(batch_results, key=lambda x: x[0])
            postprocess_concurrency = _compute_effective_batch_concurrency(
                len(ordered_batch_results),
                int(settings.batch_postprocess_concurrency),
            )
            postprocess_sem = asyncio.Semaphore(postprocess_concurrency)

            async def _persist_one_batch_result(
                item_idx: int,
                one_report: Any,
                perception_snapshot: Any,
                cognitive_snapshot: Any,
                one_student_id: str,
            ) -> Any:
                async with postprocess_sem:
                    await save_grading_result(
                        db_path,
                        task_id,
                        one_student_id,
                        one_report,
                        perception_output=perception_snapshot,
                        cognitive_output=cognitive_snapshot,
                    )

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
                    return one_report

            postprocess_jobs = [
                _persist_one_batch_result(
                    item_idx,
                    one_report,
                    perception_snapshot,
                    cognitive_snapshot,
                    one_student_id,
                )
                for item_idx, one_report, perception_snapshot, cognitive_snapshot, one_student_id in ordered_batch_results
            ]

            async def _run_batch_postprocess_with_progress() -> List[Any]:
                processed_reports: List[Any] = []
                total = len(postprocess_jobs)
                completed_count = 0
                last_emitted_count = 0
                last_emit_ts = 0.0
                for job in asyncio.as_completed(postprocess_jobs):
                    processed_reports.append(await job)
                    completed_count += 1
                    now_ts = time.monotonic()
                    if _should_emit_batch_progress(
                        completed_count=completed_count,
                        total_count=total,
                        last_emitted_count=last_emitted_count,
                        last_emit_ts=last_emit_ts,
                        now_ts=now_ts,
                    ):
                        progress = 0.80 + 0.10 * (completed_count / total)
                        await update_task_progress(db_path, task_id, progress=progress, eta_seconds=8)
                        await _publish_status(task_id, "PROCESSING", progress=progress, eta_seconds=8)
                        last_emitted_count = completed_count
                        last_emit_ts = now_ts
                return processed_reports

            reports = run_async(_run_batch_postprocess_with_progress())

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

        run_async(update_task_progress(db_path, task_id, progress=0.9, eta_seconds=5))
        run_async(_publish_status(task_id, "PROCESSING", progress=0.9, eta_seconds=5))
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

        # Step 6: Cleanup via storage adapter (Phase 32)
        storage.cleanup_task(task_id)

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
        # Cleanup on rejection
        storage.cleanup_task(task_id)
        return {"status": "rejected", "reason": str(e)}

    except Exception as e:
        # Transient failure: Retry logic
        logger.error(f"[Worker] Task {task_id} failed (attempt {self.request.retries + 1}): {e}")
        run_async(update_task_status(db_path, task_id, "FAILED", error=str(e)))
        run_async(update_task_progress(db_path, task_id, progress=1.0, eta_seconds=0))
        logger.info("task_status_persisted", extra={"extra_fields": {"status": "FAILED"}})
        # Phase 33: Publish failure event to Redis Pub/Sub
        run_async(_publish_status(task_id, "FAILED", error=str(e), progress=1.0, eta_seconds=0))

        # Cleanup on permanent failure (after max retries)
        if self.request.retries >= self.max_retries:
            storage.cleanup_task(task_id)
            
            # Phase 32: Route to Dead Letter Queue for audit
            _route_to_dlq(task_id, payload, db_path, str(e))
            
            logger.critical(f"[Worker] Task {task_id} permanently failed and routed to DLQ")
            return {"status": "failed", "error": str(e)}

        # Retry if attempts remain
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        # Permanent failure after max retries
        logger.critical(f"[Worker] Task {task_id} permanently failed after {self.max_retries} retries")
        return {"status": "failed", "error": str(e)}
    finally:
        reset_context(ctx_tokens)



async def _publish_status(task_id: str, status: str, **kwargs) -> None:
    """
    Phase 33: Publish task status update to Redis Pub/Sub.
    
    Called after database update to notify all API nodes (multi-node support).
    Non-blocking: If Pub/Sub fails, API nodes fallback to DB polling.
    
    Args:
        task_id: Business task UUID
        status: Task status (PENDING, PROCESSING, COMPLETED, FAILED)
        **kwargs: Additional event data (progress, error, message, etc.)
    """
    import redis.asyncio as aioredis
    import json
    
    redis_client = None
    try:
        redis_client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        
        channel = f"task_status:{task_id}"
        event_data = {
            "task_id": task_id,
            "status": status,
            "trace_id": get_trace_id(),
            **kwargs,
        }
        
        await redis_client.publish(channel, json.dumps(event_data))
        logger.info(f"[Worker-PubSub] Published status update for task {task_id}: {status}")
    
    except Exception as e:
        # Non-critical: SSE will fallback to DB polling
        logger.warning(f"[Worker-PubSub] Failed to publish task {task_id} status: {e}")
    
    finally:
        if redis_client:
            await redis_client.aclose()


def _route_to_dlq(task_id: str, payload: Dict[str, Any], db_path: str, error: str) -> None:
    """
    Phase 32: Route permanently failed task to Dead Letter Queue.
    
    Poison messages (tasks that crash even after max retries) are stored
    in a separate Redis queue for manual inspection and replay.
    
    Args:
        task_id: Business task UUID
        payload: Original Celery payload
        db_path: Database path
        error: Error message from final failure
    """
    import redis
    import json
    
    try:
        # Connect to Redis DLQ
        redis_client = redis.from_url(settings.redis_url)
        
        # Package task metadata for audit
        dlq_entry = {
            "task_id": task_id,
            "trace_id": get_trace_id(),
            "payload": payload,
            "db_path": db_path,
            "error": error,
            "failed_at": __import__('datetime').datetime.utcnow().isoformat(),
            "retry_count": 2,  # Max retries exhausted
        }
        
        # Push to DLQ (Redis list)
        redis_client.lpush(DLQ_QUEUE_NAME, json.dumps(dlq_entry))
        
        logger.warning(
            f"[DLQ] Task {task_id} routed to dead letter queue. "
            f"Error: {error[:100]}"
        )
        
    except Exception as dlq_error:
        logger.error(f"[DLQ] Failed to route task {task_id} to DLQ: {dlq_error}")

