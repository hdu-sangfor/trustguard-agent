from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any

import bcrypt
from fastapi import Depends, Header, HTTPException

from app.db import query

log = logging.getLogger("trustguard.gateway.auth")

AUTH_TOKEN_SECRET = os.getenv(
    "AUTH_TOKEN_SECRET",
    "trustguard-agent-dev-secret",
).encode("utf-8")
AUTH_TOKEN_TTL_SECONDS = max(
    300,
    int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "86400")),
)


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    username: str
    role: str
    status: str
    display_name: str = ""
    email: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CurrentUser:
        return cls(
            user_id=str(row.get("user_id") or ""),
            username=str(row.get("username") or ""),
            role=str(row.get("role") or "VIEWER").upper(),
            status=str(row.get("status") or ""),
            display_name=str(row.get("display_name") or ""),
            email=str(row.get("email") or ""),
        )


def get_user_by_username(username: str) -> dict[str, Any] | None:
    rows = query("SELECT * FROM tg_user WHERE username = %s", (username,))
    return rows[0] if rows else None


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    rows = query("SELECT * FROM tg_user WHERE user_id = %s", (user_id,))
    return rows[0] if rows else None


def verify_password(user: dict[str, Any], password: str) -> bool:
    hashed = user.get("password_hash")
    if isinstance(hashed, str) and hashed:
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                hashed.encode("utf-8"),
            )
        except (TypeError, ValueError):
            log.warning(
                "password hash verification failed for username=%s",
                user.get("username"),
            )
    return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _extract_token_payload(auth_header: str | None) -> dict[str, Any] | None:
    if not isinstance(auth_header, str) or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:].strip()
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        expected = hmac.new(
            AUTH_TOKEN_SECRET,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = _b64url_decode(encoded_signature)
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires_at = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= int(datetime.now(timezone.utc).timestamp()):
        return None
    return payload


def issue_token(user: dict[str, Any]) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": str(user.get("username") or ""),
        "uid": str(user.get("user_id") or ""),
        "role": str(user.get("role") or "VIEWER").upper(),
        "iat": now,
        "exp": now + AUTH_TOKEN_TTL_SECONDS,
        "jti": uuid.uuid4().hex,
    }
    encoded_payload = _b64url_encode(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = hmac.new(
        AUTH_TOKEN_SECRET,
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_b64url_encode(signature)}"


def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    payload = _extract_token_payload(authorization)
    username = str(payload.get("sub") or "").strip() if payload else ""
    if not username:
        raise HTTPException(
            status_code=401,
            detail="缺少、过期或无效的登录凭证",
        )
    row = get_user_by_username(username)
    if not row or row.get("status") != "ACTIVE":
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return CurrentUser.from_row(row)


def require_roles(*roles: str) -> Callable[..., CurrentUser]:
    allowed_roles = frozenset(role.upper() for role in roles)

    def dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail="当前角色没有执行此操作的权限",
            )
        return user

    return dependency
