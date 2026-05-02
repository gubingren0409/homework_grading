import uuid
import json
import logging
import fitz
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Request

from src.api.dependencies import get_db_path, limiter
from src.core.config import settings
from src.db.client import (
    get_rubric,
    list_rubrics,
    save_rubric,
    save_rubric_bundle,
    get_recent_rubric_by_fingerprint,
    append_rubric_generate_audit,
)
from src.core.trace_context import get_trace_id
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.rubric_bundle_workflow import RubricBundleWorkflow
from src.orchestration.rubric_selection import (
    parse_question_ids,
    validate_rubric_solution_content,
)
from src.orchestration.workflow import GradingWorkflow
from src.schemas.rubric_ir import RubricBundle, TeacherRubric
from src.skills.service import SkillService
from src.perception.factory import create_perception_engine
from src.utils.file_parsers import UnsupportedFormatError, process_multiple_files
from src.core.exceptions import GradingSystemError
from src.api.route_helpers import (
    compute_source_fingerprint as _compute_source_fingerprint,
    error_detail as _error_detail,
    request_client_ip as _request_client_ip,
)
from src.api.route_models import (
    RubricDetailResponse,
    RubricBundleGenerateResponse,
    RubricGenerateResponse,
    RubricSummaryItem,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_embedded_pdf_text(files_data: list[tuple[bytes, str]]) -> str | None:
    if not files_data or any(not filename.lower().endswith(".pdf") for _, filename in files_data):
        return None

    pages: list[str] = []
    for content, _ in files_data:
        with fitz.open(stream=content, filetype="pdf") as document:
            pages.extend(page.get_text("text") for page in document)
    text = "\n".join(page.strip() for page in pages if page.strip()).strip()
    if len(text) < 200 or "答案" not in text:
        return None
    return text


@router.post("/rubric/generate", response_model=RubricGenerateResponse, status_code=201)
@limiter.limit("5/minute")
async def generate_rubric_job(
    request: Request,
    files: List[UploadFile] = File(...),
    force_regenerate: bool = Form(False),
    db_path: str = Depends(get_db_path),
):
    """
    双轨上传-轨道1：
    上传标准答案图片/PDF，直接生成并持久化 rubric，返回 rubric_id。
    """
    files_data = []
    for file in files:
        content = await file.read()
        files_data.append((content, file.filename))

    source_fingerprint = _compute_source_fingerprint(files_data)
    trace_id = get_trace_id()
    client_ip = _request_client_ip(request)
    user_agent = request.headers.get("user-agent")
    referer = request.headers.get("referer")
    if not force_regenerate:
        cached = await get_recent_rubric_by_fingerprint(
            db_path,
            source_fingerprint=source_fingerprint,
            within_seconds=settings.rubric_dedupe_window_seconds,
        )
        if cached:
            logger.warning(
                "rubric_generation_dedup_hit",
                extra={
                    "extra_fields": {
                        "rubric_id": cached["rubric_id"],
                        "dedupe_window_seconds": settings.rubric_dedupe_window_seconds,
                    }
                },
            )
            row = await get_rubric(db_path, cached["rubric_id"])
            grading_points_count = 0
            if row:
                rubric_payload = json.loads(row["rubric_json"])
                grading_points_count = len(rubric_payload.get("grading_points", []))
            await append_rubric_generate_audit(
                db_path,
                trace_id=trace_id,
                rubric_id=cached["rubric_id"],
                source_fingerprint=source_fingerprint,
                reused_from_cache=True,
                force_regenerate=force_regenerate,
                source_file_count=len(files),
                client_ip=client_ip,
                user_agent=user_agent,
                referer=referer,
            )
            return RubricGenerateResponse(
                rubric_id=cached["rubric_id"],
                question_id=cached.get("question_id"),
                grading_points_count=grading_points_count,
                source_file_count=len(files),
                reused_from_cache=True,
            )

    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    workflow = GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent,
        skill_service=SkillService(db_path=db_path),
    )

    try:
        rubric: TeacherRubric = await workflow.generate_rubric_pipeline(files_data)
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message=str(exc),
                retryable=False,
                next_action="adjust_file",
            ),
        ) from exc
    except RuntimeError as exc:
        if "PHASE35_CONTRACT_BLOCK" in str(exc):
            detail_text = str(exc)
            raise HTTPException(
                status_code=503,
                detail=_error_detail(
                    error_code="UPSTREAM_UNAVAILABLE",
                    message=f"Rubric generation upstream unavailable: {detail_text}",
                    retryable=True,
                    retry_hint="retry_submit",
                    next_action="retry_upload",
                ),
            ) from exc
        raise
    except GradingSystemError as exc:
        error_text = str(exc)
        if "LLM egress disabled by configuration" in error_text:
            raise HTTPException(
                status_code=503,
                detail=_error_detail(
                    error_code="EGRESS_DISABLED",
                    message=error_text,
                    retryable=False,
                    next_action="enable_llm_egress",
                ),
            ) from exc
        raise HTTPException(
            status_code=503,
            detail=_error_detail(
                error_code="UPSTREAM_UNAVAILABLE",
                message=f"Rubric generation upstream unavailable: {error_text}",
                retryable=True,
                retry_hint="retry_submit",
                next_action="retry_upload",
            ),
        ) from exc
    rubric_id = str(uuid.uuid4())
    await save_rubric(
        db_path,
        rubric_id=rubric_id,
        question_id=rubric.question_id,
        rubric_json=rubric.model_dump(),
        source_fingerprint=source_fingerprint,
    )
    await append_rubric_generate_audit(
        db_path,
        trace_id=trace_id,
        rubric_id=rubric_id,
        source_fingerprint=source_fingerprint,
        reused_from_cache=False,
        force_regenerate=force_regenerate,
        source_file_count=len(files),
        client_ip=client_ip,
        user_agent=user_agent,
        referer=referer,
    )
    return RubricGenerateResponse(
        rubric_id=rubric_id,
        question_id=rubric.question_id,
        grading_points_count=len(rubric.grading_points),
        source_file_count=len(files),
        reused_from_cache=False,
    )


@router.post("/rubric/bundle/generate", response_model=RubricBundleGenerateResponse, status_code=201)
@limiter.limit("5/minute")
async def generate_rubric_bundle(
    request: Request,
    files: List[UploadFile] = File(...),
    reference_mode: str = Form("printed"),
    expected_question_ids: Optional[str] = Form(default=None),
    require_solution_content: bool = Form(default=True),
    db_path: str = Depends(get_db_path),
):
    del request
    files_data = []
    for file in files:
        content = await file.read()
        files_data.append((content, file.filename))

    normalized_reference_mode = reference_mode.strip().lower()
    if normalized_reference_mode not in {"printed", "handwritten"}:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="INPUT_REJECTED",
                message="reference_mode must be 'printed' or 'handwritten'.",
                retryable=False,
                next_action="adjust_file",
            ),
        )

    perception_engine = create_perception_engine()
    skill_service = SkillService(db_path=db_path)
    workflow = RubricBundleWorkflow(
        perception_engine=perception_engine,
        skill_service=skill_service,
        cognitive_agent=DeepSeekCognitiveEngine(),
    )
    bundle_id = str(uuid.uuid4())
    paper_id = f"paper-{bundle_id[:8]}"

    try:
        embedded_pdf_text = (
            _extract_embedded_pdf_text(files_data)
            if normalized_reference_mode == "printed"
            else None
        )
        if embedded_pdf_text:
            bundle = await workflow.generate_from_printed_reference_text(
                embedded_pdf_text,
                paper_id=paper_id,
            )
        else:
            try:
                image_bytes_list = await process_multiple_files(files_data)
            except UnsupportedFormatError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=_error_detail(
                        error_code="INPUT_REJECTED",
                        message=str(exc),
                        retryable=False,
                        next_action="adjust_file",
                    ),
                ) from exc
            if normalized_reference_mode == "handwritten":
                bundle = await workflow.generate_from_handwritten_reference(image_bytes_list, paper_id=paper_id)
            else:
                bundle = await workflow.generate_from_printed_reference(image_bytes_list, paper_id=paper_id)
        expected_ids = parse_question_ids(expected_question_ids)
        if expected_ids and require_solution_content:
            validate_rubric_solution_content(bundle, expected_ids)
    except GradingSystemError as exc:
        raise HTTPException(
            status_code=503,
            detail=_error_detail(
                error_code="UPSTREAM_UNAVAILABLE",
                message=f"Rubric bundle generation upstream unavailable: {str(exc)}",
                retryable=True,
                retry_hint="retry_submit",
                next_action="retry_upload",
            ),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=_error_detail(
                error_code="UPSTREAM_UNAVAILABLE",
                message=f"Rubric bundle generation failed: {str(exc)}",
                retryable=True,
                retry_hint="retry_submit",
                next_action="retry_upload",
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                error_code="RUBRIC_BUNDLE_INVALID",
                message=str(exc),
                retryable=False,
                next_action="inspect_reference_pdf",
            ),
        ) from exc

    source_fingerprint = _compute_source_fingerprint(files_data)
    await save_rubric_bundle(
        db_path,
        bundle_id=bundle_id,
        paper_id=bundle.paper_id,
        bundle_json=bundle.model_dump(),
        source_fingerprint=source_fingerprint,
    )
    return RubricBundleGenerateResponse(
        bundle_id=bundle_id,
        paper_id=bundle.paper_id,
        question_count=len(bundle.rubrics),
        source_file_count=len(files),
        bundle_json=bundle.model_dump(),
    )


@router.get("/rubrics", response_model=List[RubricSummaryItem])
async def get_rubric_list(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db_path: str = Depends(get_db_path),
):
    offset = (page - 1) * limit
    rows = await list_rubrics(db_path, limit=limit, offset=offset)
    return [RubricSummaryItem(**r) for r in rows]


@router.get("/rubrics/{rubric_id}", response_model=RubricDetailResponse)
async def get_rubric_detail(
    rubric_id: str,
    db_path: str = Depends(get_db_path),
):
    row = await get_rubric(db_path, rubric_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(
                error_code="RUBRIC_NOT_FOUND",
                message="Rubric not found",
                retryable=False,
                next_action="select_valid_rubric",
            ),
        )
    rubric_raw = row.get("rubric_json")
    try:
        rubric_obj = json.loads(rubric_raw) if isinstance(rubric_raw, str) else rubric_raw
    except Exception:
        rubric_obj = {}
    return RubricDetailResponse(
        rubric_id=row["rubric_id"],
        question_id=row.get("question_id"),
        created_at=row.get("created_at"),
        rubric_json=rubric_obj,
    )
