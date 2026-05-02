from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import logging
from pathlib import Path

from src.api.routes import router as grading_router
from src.db.client import init_db
from src.api.dependencies import get_db_path
from src.core.config import settings

# Configure logging for the API layer
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    db_path = get_db_path()
    logger.info(f"Initializing database at {db_path}...")
    await init_db(db_path)

    # Phase 10: Startup validation — fail fast on misconfiguration
    _startup_warnings = []

    # 1. Redis connectivity check
    try:
        import redis as _redis
        _r = _redis.Redis(
            host=settings.redis_host, port=settings.redis_port,
            db=settings.redis_db, socket_connect_timeout=3, socket_timeout=3,
        )
        _r.ping()
        _info = _r.info("server")
        logger.info(f"Redis OK — {settings.redis_host}:{settings.redis_port} "
                     f"(v{_info.get('redis_version', '?')})")
        _r.close()
    except Exception as e:
        _startup_warnings.append(f"Redis unreachable ({settings.redis_host}:{settings.redis_port}): {e}")
        logger.warning(f"[Startup] Redis unreachable — task dispatch will fallback to local execution: {e}")

    # 2. API key presence check
    qwen_keys = settings.parsed_qwen_keys
    deepseek_keys = settings.parsed_deepseek_keys
    if not qwen_keys:
        _startup_warnings.append("QWEN_API_KEYS not set — perception disabled")
        logger.warning("[Startup] No Qwen API keys configured — VLM perception calls will fail")
    if not deepseek_keys:
        _startup_warnings.append("DEEPSEEK_API_KEYS not set — cognitive disabled")
        logger.warning("[Startup] No DeepSeek API keys configured — cognitive grading calls will fail")

    if _startup_warnings:
        logger.warning(f"[Startup] {len(_startup_warnings)} warning(s): {'; '.join(_startup_warnings)}")

    logger.info("API Gateway is ready.")
    yield


app = FastAPI(
    title="AI Homework Grader API Gateway",
    description="Scalable async API for homework evaluation using Qwen-VL and DeepSeek.",
    version="1.0.0",
    lifespan=lifespan,
)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes ---
app.include_router(grading_router)

@app.get("/health")
async def health_check():
    """Health check endpoint with Redis connectivity status."""
    redis_ok = True
    redis_detail = ""
    try:
        import redis as _redis
        _r = _redis.Redis(
            host=settings.redis_host, port=settings.redis_port,
            db=settings.redis_db, socket_connect_timeout=2, socket_timeout=2,
        )
        _r.ping()
        _r.close()
    except Exception as exc:
        redis_ok = False
        redis_detail = str(exc)[:100]
    return {
        "status": "healthy" if redis_ok else "degraded",
        "service": "grader-api",
        "redis": {"ok": redis_ok, "target": f"{settings.redis_host}:{settings.redis_port}", "error": redis_detail or None},
    }


def _serve_console_page(filename: str) -> FileResponse:
    static_file = Path(__file__).parent / "static" / filename
    if not static_file.exists():
        raise HTTPException(status_code=404, detail="console page not found")
    return FileResponse(static_file)


@app.get("/", include_in_schema=False)
async def landing_page():
    """Product landing page — first thing visitors see."""
    return _serve_console_page("index.html")


@app.get("/review-console", include_in_schema=False)
async def review_console():
    """
    Minimal review console for:
    1) Uploading image/PDF tasks
    2) Real-time SSE status tracking
    3) Pending human-review list
    4) Submitting human feedback back to DB
    """
    static_file = Path(__file__).parent / "static" / "review_workbench.html"
    return FileResponse(static_file)


@app.get("/student-console", include_in_schema=False)
async def student_console():
    return _serve_console_page("student_console.html")


@app.get("/whole-paper-console", include_in_schema=False)
async def whole_paper_console():
    return _serve_console_page("whole_paper_console.html")


@app.get("/whole-paper-report", include_in_schema=False)
async def whole_paper_report():
    return _serve_console_page("whole_paper_report.html")


@app.get("/ops-console", include_in_schema=False)
async def ops_console():
    return _serve_console_page("ops_console.html")


@app.get("/tasks-list", include_in_schema=False)
@app.get("/task-list", include_in_schema=False)
async def tasks_list_page():
    return _serve_console_page("task_list.html")


@app.get("/history-results", include_in_schema=False)
async def history_results_page():
    return _serve_console_page("history_results.html")


@app.get("/report-view", include_in_schema=False)
async def report_view_page():
    return _serve_console_page("report_view.html")


@app.get("/class-dashboard", include_in_schema=False)
async def class_dashboard_page():
    return _serve_console_page("class_dashboard.html")


@app.get("/task-progress", include_in_schema=False)
async def task_progress_page():
    return _serve_console_page("task_progress.html")


@app.get("/student-console-batch", include_in_schema=False)
async def student_console_batch_page():
    return _serve_console_page("student_console_batch.html")


@app.get("/login", include_in_schema=False)
async def login_page():
    return _serve_console_page("login.html")
