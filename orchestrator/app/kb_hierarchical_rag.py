"""
kb-r3c：层级 RAG MVP — 子向量点 payload 带父 chunk 指针时，从 Chunk Store 拉取父正文摘要注入 kb_hits。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def kb_hierarchical_rag_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    v = (env.get("KB_HIERARCHICAL_RAG") or "false").strip().lower()
    return v in ("1", "true", "yes", "on")


def kb_parent_chunk_max_chars(environ: dict[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = (env.get("KB_PARENT_CHUNK_MAX_CHARS") or "").strip()
    if not raw:
        return 4000
    try:
        return max(256, min(int(raw), 50000))
    except ValueError:
        return 4000


def tenant_expect_from_target_context(ctx: dict[str, Any]) -> str | None:
    for k in ("tenant_id", "_tenant_id", "workspace_id", "_workspace_id"):
        v = ctx.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def parent_chunk_refs_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """(parent_chunk_id, parent_task_id 覆盖；空则调用方用当前 task_id)。"""
    pcid = str(
        payload.get("parent_chunk_id")
        or payload.get("kb_parent_chunk_id")
        or ""
    ).strip()
    ptid = str(
        payload.get("parent_chunk_task_id")
        or payload.get("kb_parent_task_id")
        or ""
    ).strip()
    return pcid, ptid


def format_chunk_record_body(rec: dict[str, Any] | None, max_chars: int) -> str:
    if not rec or not isinstance(rec, dict):
        return ""
    content = rec.get("content")
    if not isinstance(content, dict):
        return ""
    body = content.get("body")
    if isinstance(body, str):
        s = body.strip()
    else:
        try:
            s = json.dumps(body, ensure_ascii=False)
        except Exception:
            s = str(body)
    if max_chars > 0 and len(s) > max_chars:
        return s[:max_chars].rstrip() + "…[truncated]"
    return s


async def enrich_kb_hits_with_parent_chunks(
    *,
    task_id: str,
    hit_summaries: list[dict[str, Any]],
    target_context: dict[str, Any],
) -> int:
    """
    就地写入 parent_chunk_preview / parent_chunk_resolved。
    返回成功解析父 chunk 正文（非空）的命中条数。
    """
    if not kb_hierarchical_rag_enabled():
        return 0
    tid = (task_id or "").strip()
    if not tid or not hit_summaries:
        return 0

    from app.core.chunk_store import read_chunk

    expect_tenant = tenant_expect_from_target_context(target_context)
    max_ch = kb_parent_chunk_max_chars()
    cache: dict[tuple[str, str], str] = {}
    resolved = 0

    for row in hit_summaries:
        pcid = str(row.get("parent_chunk_id") or "").strip()
        if not pcid:
            continue
        ptid = str(row.get("parent_chunk_task_id") or "").strip() or tid

        key = (ptid, pcid)
        if key in cache:
            body = cache[key]
        else:

            def _load() -> dict[str, Any] | None:
                try:
                    return read_chunk(
                        ptid,
                        pcid,
                        expect_tenant_id=expect_tenant,
                        deny_tenant_mismatch=False,
                        require_tenant_when_bound=False,
                    )
                except Exception:
                    logger.debug("kb_hierarchical_rag read_chunk failed task=%s chunk=%s", ptid, pcid)
                    return None

            rec = await asyncio.to_thread(_load)
            body = format_chunk_record_body(rec, max_ch)
            cache[key] = body

        if body:
            row["parent_chunk_preview"] = body
            row["parent_chunk_resolved"] = True
            resolved += 1
        else:
            row["parent_chunk_resolved"] = False

    return resolved
