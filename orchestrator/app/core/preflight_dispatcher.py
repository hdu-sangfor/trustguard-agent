"""dispatcher：prepare 阶段 run_id / seed_urls 注入。"""
from __future__ import annotations

import uuid
from typing import Any

from app.core.preflight_context import PreflightContext


def apply_dispatcher_preflight(params: dict[str, Any], pctx: PreflightContext) -> None:
    op = str(params.get("operation") or "prepare").strip().lower()
    if op != "prepare":
        return
    rid = str(params.get("run_id") or "").strip()
    if not rid:
        persisted = str(pctx.ctx.get(pctx.run_id_key) or "").strip()
        rid = persisted or f"preflight-{uuid.uuid4().hex[:12]}"
        params["run_id"] = rid
        pctx.ctx[pctx.run_id_key] = rid
    if pctx.seeds and not params.get("seed_urls"):
        params["seed_urls"] = pctx.seeds[:64]
