"""
P9-01: Minimal auth shell for teacher identity.

Design:
- JWT-based token stored in browser localStorage
- When auth_enabled=False (dev mode), all requests get a default teacher identity
- When auth_enabled=True, requests without valid token get HTTP 401
- Frontend pages redirect to /login on 401
- teacher_id is injected into request state for downstream use
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import settings

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"

# Pre-defined teacher accounts for trial phase.
# In production, this would come from a database or external IdP.
TRIAL_TEACHERS = {
    "teacher-demo": {"name": "演示教师", "password": "demo2024"},
    "teacher-a": {"name": "张老师", "password": "zhanglaoshi"},
    "teacher-b": {"name": "李老师", "password": "lilaoshi"},
}

_bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(teacher_id: str, teacher_name: str) -> str:
    payload = {
        "sub": teacher_id,
        "name": teacher_name,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.auth_token_expire_minutes),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.auth_secret_key, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


class TeacherIdentity:
    """Lightweight identity object attached to request.state."""
    __slots__ = ("teacher_id", "teacher_name")

    def __init__(self, teacher_id: str, teacher_name: str):
        self.teacher_id = teacher_id
        self.teacher_name = teacher_name

    def __repr__(self) -> str:
        return f"TeacherIdentity(id={self.teacher_id!r}, name={self.teacher_name!r})"


async def get_current_teacher(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> TeacherIdentity:
    """
    FastAPI dependency that resolves teacher identity.

    - auth_enabled=False: returns default teacher (dev bypass)
    - auth_enabled=True: requires valid Bearer token
    """
    if not settings.auth_enabled:
        identity = TeacherIdentity(
            teacher_id=settings.auth_default_teacher_id,
            teacher_name=settings.auth_default_teacher_name,
        )
        request.state.teacher = identity
        return identity

    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录，请先登录")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    identity = TeacherIdentity(
        teacher_id=payload.get("sub", "unknown"),
        teacher_name=payload.get("name", "未知教师"),
    )
    request.state.teacher = identity
    return identity


def authenticate_teacher(teacher_id: str, password: str) -> Optional[TeacherIdentity]:
    """Verify credentials against trial teacher list."""
    teacher = TRIAL_TEACHERS.get(teacher_id)
    if teacher and teacher["password"] == password:
        return TeacherIdentity(teacher_id=teacher_id, teacher_name=teacher["name"])
    return None
