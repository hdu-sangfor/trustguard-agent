"""
kb-r2c：管道 HTTP 触发鉴权（与 Manual 共用令牌时可只配 KB_MANUAL_INGEST_TOKEN）。
"""

from __future__ import annotations

import os


def kb_pipeline_admin_token_expected() -> str:
    return (os.getenv("KB_PIPELINE_ADMIN_TOKEN") or os.getenv("KB_MANUAL_INGEST_TOKEN") or "").strip()


def require_kb_pipeline_admin_token(
    authorization: str | None,
    x_kb_manual_ingest_token: str | None,
) -> None:
    from fastapi import HTTPException

    expected = kb_pipeline_admin_token_expected()
    if not expected:
        raise HTTPException(status_code=404, detail="kb_pipeline_admin_disabled")
    got = ""
    if x_kb_manual_ingest_token and x_kb_manual_ingest_token.strip():
        got = x_kb_manual_ingest_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        got = authorization[7:].strip()
    if not got or got != expected:
        raise HTTPException(status_code=401, detail="invalid_kb_pipeline_admin_token")
