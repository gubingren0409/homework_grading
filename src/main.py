from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
import logging

from src.api.routes import router as grading_router, limiter as api_limiter
from src.core.exceptions import PerceptionShortCircuitError, GradingSystemError
from src.core.json_logging import configure_json_logging
from src.core.trace_context import bind_context, new_trace_id, reset_context
from src.core.config import settings
from src.core.http_limits import HardBodyLimitMiddleware
from src.prompts.provider import get_prompt_provider

configure_json_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    await _prompt_provider.start()
    try:
        yield
    finally:
        await _prompt_provider.stop()


app = FastAPI(
    title="AI Homework Grader API",
    description="Scalable API for AI-driven homework evaluation using decoupled Perception and Cognition layers.",
    version="0.1.0",
    lifespan=lifespan,
)

_prompt_provider = get_prompt_provider()
_STATIC_DIR = Path(__file__).resolve().parent / "api" / "static"


class TraceContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
        task_id = request.path_params.get("task_id", "-") if hasattr(request, "path_params") else "-"
        tokens = bind_context(trace_id=trace_id, component="api-gateway")
        if task_id and task_id != "-":
            tokens.update(bind_context(task_id=task_id))
        try:
            logger.info("gateway_request_received")
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            reset_context(tokens)

# Setup Limiter state and handlers
app.state.limiter = api_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    HardBodyLimitMiddleware,
    max_body_bytes=settings.max_request_body_bytes,
    read_timeout_seconds=settings.request_body_read_timeout_seconds,
)
app.add_middleware(TraceContextMiddleware)

# Register Routes
app.include_router(grading_router)

@app.exception_handler(PerceptionShortCircuitError)
async def perception_short_circuit_handler(request: Request, exc: PerceptionShortCircuitError):
    """
    Handles hardware-level circuit breaking due to low image quality.
    Returns 422 Unprocessable Entity.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": "PerceptionShortCircuit",
            "message": exc.message,
            "readability_status": exc.readability_status,
            "suggestion": "Please ensure the image is clear and well-lit before re-uploading."
        }
    )


@app.exception_handler(GradingSystemError)
async def grading_system_error_handler(request: Request, exc: GradingSystemError):
    """
    Handles general business logic errors within the grading system.
    Returns 400 Bad Request.
    """
    return JSONResponse(
        status_code=400,
        content={
            "error": "GradingSystemError",
            "message": str(exc)
        }
    )


@app.exception_handler(RateLimitExceeded)
async def structured_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    del request
    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "error_code": "RATE_LIMITED",
                "message": "Too many requests. Please retry later.",
                "retryable": True,
                "retry_hint": "retry_later",
                "next_action": "wait_and_retry",
            }
        },
    )


@app.get("/")
async def health_check():
    """Service health check endpoint."""
    return {"status": "healthy", "service": "homework-grader-core"}


def _serve_console_page(filename: str) -> FileResponse:
    page = (_STATIC_DIR / filename).resolve()
    if not page.exists():
        raise HTTPException(status_code=404, detail="console page not found")
    return FileResponse(page)


@app.get("/student-console", include_in_schema=False)
async def student_console() -> FileResponse:
    return _serve_console_page("student_console.html")


@app.get("/student-console-batch", include_in_schema=False)
async def student_console_batch() -> FileResponse:
    return _serve_console_page("student_console_batch.html")


@app.get("/whole-paper-console", include_in_schema=False)
async def whole_paper_console() -> FileResponse:
    return _serve_console_page("whole_paper_console.html")


@app.get("/whole-paper-report", include_in_schema=False)
async def whole_paper_report() -> FileResponse:
    return _serve_console_page("whole_paper_report.html")


@app.get("/review-console", include_in_schema=False)
async def review_console() -> FileResponse:
    return _serve_console_page("review_workbench.html")


@app.get("/ops-console", include_in_schema=False)
async def ops_console() -> FileResponse:
    return _serve_console_page("ops_console.html")


@app.get("/tasks-list", include_in_schema=False)
async def tasks_list_page() -> FileResponse:
    return _serve_console_page("task_list.html")


@app.get("/task-progress", include_in_schema=False)
async def task_progress_page() -> FileResponse:
    return _serve_console_page("task_progress.html")


@app.get("/class-dashboard", include_in_schema=False)
async def class_dashboard_page() -> FileResponse:
    return _serve_console_page("class_dashboard.html")


@app.get("/history-results", include_in_schema=False)
async def history_results_page() -> FileResponse:
    return _serve_console_page("history_results.html")


@app.get("/demo-showcase", include_in_schema=False)
async def demo_showcase_page() -> FileResponse:
    return _serve_console_page("demo_showcase.html")


@app.get("/report-view", include_in_schema=False)
async def report_view_page() -> FileResponse:
    return _serve_console_page("report_view.html")
