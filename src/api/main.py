from fastapi import FastAPI
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

app = FastAPI(
    title="AI Homework Grader API Gateway",
    description="Scalable async API for homework evaluation using Qwen-VL and DeepSeek.",
    version="1.0.0"
)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Lifecycle ---
@app.on_event("startup")
async def startup_event():
    """Perform database initialization on boot."""
    db_path = get_db_path()
    logger.info(f"Initializing database at {db_path}...")
    await init_db(db_path)
    logger.info("API Gateway is ready.")

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
    static_file = Path(__file__).parent / "static" / "review_console.html"
    return FileResponse(static_file)
