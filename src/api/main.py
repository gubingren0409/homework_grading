from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import logging
from pathlib import Path

from src.api.routes import router as grading_router
from src.db.client import init_db
from src.api.dependencies import get_db_path

# Configure logging for the API layer
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    db_path = get_db_path()
    logger.info(f"Initializing database at {db_path}...")
    await init_db(db_path)
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
    """Basic health check endpoint."""
    return {"status": "healthy", "service": "grader-api"}


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


def _serve_console_page(filename: str) -> FileResponse:
    static_file = Path(__file__).parent / "static" / filename
    if not static_file.exists():
        raise HTTPException(status_code=404, detail="console page not found")
    return FileResponse(static_file)


@app.get("/student-console", include_in_schema=False)
async def student_console():
    return _serve_console_page("student_console.html")


@app.get("/ops-console", include_in_schema=False)
async def ops_console():
    return _serve_console_page("ops_console.html")


@app.get("/tasks-list", include_in_schema=False)
async def tasks_list_page():
    return _serve_console_page("task_list.html")


@app.get("/history-results", include_in_schema=False)
async def history_results_page():
    return _serve_console_page("history_results.html")


@app.get("/report-view", include_in_schema=False)
async def report_view_page():
    return _serve_console_page("report_view.html")


@app.get("/login", include_in_schema=False)
async def login_page():
    return _serve_console_page("login.html")
