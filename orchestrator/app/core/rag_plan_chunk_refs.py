"""
kb-r5a：将 KB 检索命中中的 chunk_id 按 skill 对齐写入 PlanItem.context_chunk_refs（去重、受上限约束）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Sequence

from app.core.plan_business_validate import _resolve_skill, plan_max_chunk_refs_per_item
from app.plan_models import ContextChunkRef, PlanItem, PlanList, PlanSchemaVersion

logger = logging.getLogger(__name__)


def rag_merge_chunk_refs_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    v = (env.get("ORCH_RAG_MERGE_CHUNK_REFS") or "true").strip().lower()
    return v not in ("0", "false", "no", "off")


def merge_kb_hits_into_plan_chunk_refs(
    plan_list: PlanList,
    kb_hits: list[dict[str, Any]] | None,
    *,
    available_skill_ids: Sequence[str],
    skill_aliases: Mapping[str, str] | None = None,
    max_refs_per_item: int | None = None,
) -> PlanList:
    """
    对每个 PlanItem：保留原有 context_chunk_refs 顺序；按 kb_hits 顺序追加
    source=experience 且 skill 与该项一致、且含非空 chunk_id 的引用；chunk_id 去重；总长不超过 cap。
    """
    if not rag_merge_chunk_refs_enabled():
        return plan_list
    hits = kb_hits or []
    if not hits:
        return plan_list
    cap = max_refs_per_item if max_refs_per_item is not None else plan_max_chunk_refs_per_item()
    if cap == 0:
        return plan_list

    avail = frozenset(available_skill_ids or [])
    new_items: list[PlanItem] = []
    total_appended = 0

    for item in plan_list.items or []:
        merged = _merge_chunk_refs_for_plan_item(
            item,
            hits,
            available_skill_ids=available_skill_ids,
            skill_aliases=skill_aliases,
            cap=cap,
        )
        added = max(0, len(merged) - len(item.context_chunk_refs or []))
        total_appended += added
        new_items.append(item.model_copy(update={"context_chunk_refs": merged}))

    if total_appended:
        logger.info(
            "kb-r5a merged RAG chunk refs into plan_list task_id=%s appended_total=%s cap=%s",
            plan_list.task_id,
            total_appended,
            cap,
        )

    return plan_list.model_copy(update={"items": new_items})


def _merge_chunk_refs_for_plan_item(
    item: PlanItem,
    kb_hits: list[dict[str, Any]],
    *,
    available_skill_ids: Sequence[str],
    skill_aliases: Mapping[str, str] | None,
    cap: int,
) -> list[ContextChunkRef]:
    resolved_item = _resolve_skill(item.skill_id, available_skill_ids, skill_aliases)

    out: list[ContextChunkRef] = list(item.context_chunk_refs or [])
    seen = {ref.chunk_id for ref in out}

    for hit in kb_hits:
        if len(out) >= cap:
            break
        if (hit.get("source") or "") != "experience":
            continue
        cid = str(hit.get("chunk_id") or "").strip()
        if not cid or cid in seen:
            continue
        hit_skill_raw = str(hit.get("skill_id") or "").strip()
        resolved_hit = _resolve_skill(hit_skill_raw, available_skill_ids, skill_aliases)
        if resolved_hit != resolved_item:
            continue

        tenant_raw = hit.get("workspace_id") or hit.get("tenant_id")
        tenant_id = str(tenant_raw).strip() if tenant_raw else None
        out.append(
            ContextChunkRef(
                schema_version=PlanSchemaVersion.V1,
                chunk_id=cid,
                tenant_id=tenant_id or None,
            )
        )
        seen.add(cid)

    return out
