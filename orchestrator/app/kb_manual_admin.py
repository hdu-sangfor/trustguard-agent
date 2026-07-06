"""
kb-r2b：Manual 静态知识录入与审核（需 KB_MANUAL_INGEST_TOKEN）。
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field


class ManualKnowledgeIngestBody(BaseModel):
    """录入正文；默认 review_status=approved（持令牌即视为已审）。待审核流可置 pending 再由 approve 接口放行。"""

    body: str = Field(..., min_length=1, max_length=80000)
    title: str = Field(default="", max_length=2048)
    embed_text: str | None = Field(default=None, max_length=8000)
    phase: str = Field(default="RECON", max_length=64)
    project_id: str | None = Field(default=None, max_length=512)
    workspace_id: str | None = Field(default=None, max_length=512)
    tags: list[str] = Field(default_factory=list)
    source_url: str | None = Field(default=None, max_length=4096)
    reviewed_by: str | None = Field(default=None, max_length=256)
    last_reviewed_at: str | None = Field(default=None, max_length=64)
    tool_id: str = Field(default="manual_admin", max_length=128)
    doc_key: str | None = Field(default=None, max_length=512)
    review_status: Literal["pending", "approved"] = "approved"
    context_snapshot: dict[str, Any] | None = None


class ManualKnowledgeApproveBody(BaseModel):
    point_id: str = Field(..., min_length=1, max_length=128)
    reviewed_by: str | None = Field(default=None, max_length=256)


def kb_manual_ingest_token_expected() -> str:
    return (os.getenv("KB_MANUAL_INGEST_TOKEN") or "").strip()


def require_kb_manual_ingest_token(
    authorization: str | None,
    x_kb_manual_ingest_token: str | None,
) -> None:
    from fastapi import HTTPException

    expected = kb_manual_ingest_token_expected()
    if not expected:
        raise HTTPException(status_code=404, detail="kb_manual_ingest_disabled")
    got = ""
    if x_kb_manual_ingest_token and x_kb_manual_ingest_token.strip():
        got = x_kb_manual_ingest_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        got = authorization[7:].strip()
    if not got or got != expected:
        raise HTTPException(status_code=401, detail="invalid_kb_manual_ingest_token")


async def run_manual_knowledge_ingest(req: ManualKnowledgeIngestBody) -> dict[str, Any]:
    from fastapi import HTTPException

    from app.clients.kb_client import QdrantKBClient, get_kb_client, get_kb_config, manual_knowledge_target_collection
    from app.clients.trace_client import emit_trace
    from app.enums import coerce_phase_from_llm
    from app.models import TraceEvent

    try:
        phase_norm = coerce_phase_from_llm(req.phase).value
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_phase") from None

    cfg = get_kb_config()
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="kb_disabled")

    client = get_kb_client()
    if not isinstance(client, QdrantKBClient):
        raise HTTPException(status_code=503, detail="kb_manual_ingest_requires_qdrant")

    from app.kb_embed_quota import EmbedQuotaExceeded

    try:
        point_id = await client.upsert_manual_knowledge_document(
            title=req.title,
            body=req.body,
            embed_text=req.embed_text,
            phase=phase_norm,
            project_id=req.project_id,
            workspace_id=req.workspace_id,
            tags=req.tags,
            source_url=req.source_url,
            reviewed_by=req.reviewed_by,
            last_reviewed_at=req.last_reviewed_at,
            tool_id=req.tool_id,
            doc_key=req.doc_key,
            review_status=req.review_status,
            context_snapshot=req.context_snapshot,
        )
    except EmbedQuotaExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={"code": "KB_EMBED_QUOTA", "reason": e.reason},
        ) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception:
        raise HTTPException(status_code=500, detail="kb_manual_ingest_failed") from None

    if not point_id:
        raise HTTPException(status_code=500, detail="kb_manual_ingest_failed_empty_id")

    col = manual_knowledge_target_collection(cfg)
    try:
        from datetime import datetime

        await emit_trace(
            TraceEvent(
                task_id="kb-admin",
                timestamp=datetime.utcnow().isoformat() + "Z",
                event_type="KB_MANUAL_INGEST",
                source_module="orchestrator",
                payload={
                    "point_id": point_id,
                    "collection": col,
                    "phase": phase_norm,
                    "review_status": req.review_status,
                    "project_id": req.project_id,
                    "workspace_id": req.workspace_id,
                    "doc_key": req.doc_key,
                },
            )
        )
    except Exception:
        pass

    return {"point_id": point_id, "collection": col, "review_status": req.review_status}


async def run_manual_knowledge_approve(req: ManualKnowledgeApproveBody) -> dict[str, Any]:
    from fastapi import HTTPException

    from app.clients.kb_client import QdrantKBClient, get_kb_client, get_kb_config
    from app.clients.trace_client import emit_trace
    from app.models import TraceEvent

    cfg = get_kb_config()
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="kb_disabled")

    client = get_kb_client()
    if not isinstance(client, QdrantKBClient):
        raise HTTPException(status_code=503, detail="kb_manual_ingest_requires_qdrant")

    try:
        ok, col = await client.approve_manual_knowledge_review(
            point_id=req.point_id,
            reviewed_by=req.reviewed_by,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if not ok:
        raise HTTPException(
            status_code=404,
            detail="manual_knowledge_point_not_found_or_not_manual",
        )

    try:
        from datetime import datetime

        await emit_trace(
            TraceEvent(
                task_id="kb-admin",
                timestamp=datetime.utcnow().isoformat() + "Z",
                event_type="KB_MANUAL_REVIEW_APPROVED",
                source_module="orchestrator",
                payload={
                    "point_id": req.point_id.strip(),
                    "collection": col,
                    "reviewed_by": req.reviewed_by,
                },
            )
        )
    except Exception:
        pass

    return {"point_id": req.point_id.strip(), "collection": col, "review_status": "approved"}
