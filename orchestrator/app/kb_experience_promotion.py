"""
kb-r4b：经验 effectiveness 异步晋升（pending → proven）。

- 决策路径中 RAG 最终注入的 pending 经验点 id 被计数；
- 后台间隔 `KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS` 执行 sweep，达到
  `KB_EXPERIENCE_PROMOTE_RECALLS` 次且 Qdrant 中仍为 pending 则 set_payload 为 proven；
- 发 `KB_EXPERIENCE_EFFECTIVENESS_PROMOTED` trace（含 doc_id/point_id、reason、task_id）。

单实例进程内状态；多副本需后续接共享存储或队列。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_counts: dict[str, int] = {}
_last_task: dict[str, str] = {}
_lock = asyncio.Lock()


def kb_experience_promotion_interval_seconds() -> int:
    return max(0, int(os.getenv("KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS", "0") or "0"))


def kb_experience_promote_recall_threshold() -> int:
    return max(1, int(os.getenv("KB_EXPERIENCE_PROMOTE_RECALLS", "3") or "3"))


def kb_experience_promotion_rollback_enabled() -> bool:
    """运维回滚：为 true 时不执行晋升写 Qdrant（计数仍累计，便于演练与对账）。"""
    v = (os.getenv("KB_EXPERIENCE_PROMOTION_ROLLBACK") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def reset_promotion_state_for_tests() -> None:
    _counts.clear()
    _last_task.clear()


async def note_kb_experience_surfaces(task_id: str, pending_point_ids: list[str]) -> None:
    """对进入本轮 RAG 注入结果的 pending 经验点累加召回计数。"""
    if kb_experience_promotion_interval_seconds() <= 0:
        return
    tid = (task_id or "").strip()
    if not tid:
        return
    seen: set[str] = set()
    async with _lock:
        for pid in pending_point_ids:
            p = (pid or "").strip()
            if not p or p in seen:
                continue
            seen.add(p)
            _counts[p] = _counts.get(p, 0) + 1
            _last_task[p] = tid


def _retrieve_payload(
    client: Any,
    *,
    collection: str,
    point_id: str,
) -> dict[str, Any] | None:
    try:
        rows = client.retrieve(collection_name=collection, ids=[point_id], with_payload=True)
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    pl = getattr(row, "payload", None) or {}
    return pl if isinstance(pl, dict) else None


async def _try_promote_pending_to_proven(
    *,
    qdrant_url: str,
    collection: str,
    point_id: str,
    recall_count: int,
    source_task_id: str,
) -> bool:
    try:
        from qdrant_client import QdrantClient
    except Exception:
        logger.warning("kb_experience_promotion: qdrant_client not available")
        return False

    def _run() -> bool:
        client = QdrantClient(url=qdrant_url)
        pl = _retrieve_payload(client, collection=collection, point_id=point_id)
        if not pl:
            return False
        eff = str(pl.get("effectiveness") or "").strip().lower()
        if eff != "pending":
            return False
        now = _iso_now()
        client.set_payload(
            collection_name=collection,
            payload={
                "effectiveness": "proven",
                "updated_at": now,
            },
            points=[point_id],
        )
        return True

    try:
        ok = await asyncio.to_thread(_run)
    except Exception:
        logger.exception("kb_experience_promotion: promote failed point_id=%s", point_id)
        return False

    if ok:
        try:
            from app.clients.trace_client import emit_trace
            from app.models import TraceEvent

            await emit_trace(
                TraceEvent(
                    task_id=(source_task_id or "system").strip() or "system",
                    timestamp=_iso_now(),
                    event_type="KB_EXPERIENCE_EFFECTIVENESS_PROMOTED",
                    source_module="orchestrator",
                    payload={
                        "point_id": point_id,
                        "doc_id": point_id,
                        "from_effectiveness": "pending",
                        "to_effectiveness": "proven",
                        "reason": "rag_recall_threshold",
                        "recall_count": recall_count,
                        "collection": collection,
                    },
                )
            )
        except Exception:
            pass
    return ok


async def sweep_kb_experience_promotions() -> None:
    if kb_experience_promotion_interval_seconds() <= 0:
        return
    from app.clients.kb_client import get_kb_config

    cfg = get_kb_config()
    if not cfg.enabled:
        return
    threshold = kb_experience_promote_recall_threshold()
    async with _lock:
        candidates = [(pid, n, _last_task.get(pid, "")) for pid, n in list(_counts.items()) if n >= threshold]
    if not candidates:
        return

    rollback = kb_experience_promotion_rollback_enabled()
    promoted_ok = 0
    promoted_fail = 0
    rollback_skips = 0
    for point_id, n, tid in candidates:
        if rollback:
            rollback_skips += 1
            async with _lock:
                _counts.pop(point_id, None)
                _last_task.pop(point_id, None)
            logger.info(
                "kb_experience_promotion: rollback enabled, skipping point_id=%s recall_count=%s",
                point_id,
                n,
            )
            continue
        ok = await _try_promote_pending_to_proven(
            qdrant_url=cfg.qdrant_url,
            collection=cfg.experience_collection,
            point_id=point_id,
            recall_count=n,
            source_task_id=tid,
        )
        async with _lock:
            _counts.pop(point_id, None)
            _last_task.pop(point_id, None)
        if ok:
            promoted_ok += 1
        else:
            promoted_fail += 1
            logger.info(
                "kb_experience_promotion: skipped or failed point_id=%s (no longer pending or missing)",
                point_id,
            )

    try:
        from app.orchestrator_sli_metrics import record_kb_experience_promotion_sweep

        record_kb_experience_promotion_sweep(
            candidates=len(candidates),
            promoted_ok=promoted_ok,
            promoted_fail=promoted_fail,
            rollback_skips=rollback_skips,
        )
    except Exception:
        pass
