"""
P9-01: Auth router — login/logout/whoami endpoints.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import (
    TeacherIdentity,
    authenticate_teacher,
    create_access_token,
    TRIAL_TEACHERS,
)
from src.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    teacher_id: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    token: str
    teacher_id: str
    teacher_name: str
    expires_in_minutes: int


class WhoAmIResponse(BaseModel):
    auth_enabled: bool
    teacher_id: str
    teacher_name: str


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """Authenticate a teacher and return a JWT token."""
    identity = authenticate_teacher(body.teacher_id, body.password)
    if identity is None:
        raise HTTPException(status_code=401, detail="账号或密码错误")

    token = create_access_token(identity.teacher_id, identity.teacher_name)
    logger.info("teacher_login", extra={"extra_fields": {"teacher_id": identity.teacher_id}})

    return LoginResponse(
        token=token,
        teacher_id=identity.teacher_id,
        teacher_name=identity.teacher_name,
        expires_in_minutes=settings.auth_token_expire_minutes,
    )


@router.get("/whoami", response_model=WhoAmIResponse)
async def whoami():
    """
    Lightweight probe — does NOT require a token.
    Returns auth_enabled flag so frontend knows whether to show login.
    When auth is disabled, returns the default teacher identity.
    """
    if not settings.auth_enabled:
        return WhoAmIResponse(
            auth_enabled=False,
            teacher_id=settings.auth_default_teacher_id,
            teacher_name=settings.auth_default_teacher_name,
        )
    return WhoAmIResponse(
        auth_enabled=True,
        teacher_id="",
        teacher_name="",
    )
