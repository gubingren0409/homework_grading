from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
import logging

from src.api.routes import router as grading_router
from src.core.exceptions import PerceptionShortCircuitError, GradingSystemError
from src.core.json_logging import configure_json_logging
from src.core.trace_context import bind_context, new_trace_id, reset_context
from src.core.config import settings
from src.core.http_limits import HardBodyLimitMiddleware

configure_json_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="AI Homework Grader API",
    description="Scalable API for AI-driven homework evaluation using decoupled Perception and Cognition layers.",
    version="0.1.0"
)


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
app.state.limiter = limiter
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


@app.get("/")
async def health_check():
    """Service health check endpoint."""
    return {"status": "healthy", "service": "homework-grader-core"}
