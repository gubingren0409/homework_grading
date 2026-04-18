"""
Aggregated API router — thin layer that includes domain-specific routers.

All handler implementations live in ``src.api.routers.*``.
This file exists solely to keep ``from src.api.routes import router``
working everywhere (main.py, tests, etc.) without any caller changes.
"""

import logging

from fastapi import APIRouter

from src.api.dependencies import limiter  # shared instance; main.py reads it as api_limiter

from src.api.routers.auth import router as auth_router
from src.api.routers.grade import router as grade_router
from src.api.routers.rubric import router as rubric_router
from src.api.routers.review import router as review_router
from src.api.routers.meta import router as meta_router
from src.api.routers.ops import router as ops_router
from src.api.routers.skills import router as skills_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["grading"])

router.include_router(auth_router)
router.include_router(rubric_router)
router.include_router(grade_router)
router.include_router(review_router)
router.include_router(meta_router)
router.include_router(ops_router)
router.include_router(skills_router)
