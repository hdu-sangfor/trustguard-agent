"""
R6d：派发层按 target / workspace（租户）维度的在途并发上限，与 ExecutionDispatcher 对齐。

- `DISPATCH_MAX_INFLIGHT_PER_TARGET`：同一规范化 target（URL 等）全局在途执行数上限；0=关闭。
- `DISPATCH_MAX_INFLIGHT_PER_WORKSPACE`：同一 workspace_id / project_id 全局在途上限；0=关闭。

与 `MAX_IN_FLIGHT_SKILLS_PER_TASK`（每 task）正交：先占 task 槽，再占 target/workspace 槽。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.task_store import TaskStore
from app.models import ExecuteSkillResponse

logger = logging.getLogger(__name__)

_SCOPE_KEY_RE = re.compile(r"^[-_a-zA-Z0-9.]{1,200}$")


def dispatch_max_inflight_per_target() -> int:
    try:
        return max(0, int(os.getenv("DISPATCH_MAX_INFLIGHT_PER_TARGET", "0") or "0"))
    except ValueError:
        return 0


def dispatch_max_inflight_per_workspace() -> int:
    try:
        return max(0, int(os.getenv("DISPATCH_MAX_INFLIGHT_PER_WORKSPACE", "0") or "0"))
    except ValueError:
        return 0


def rate_limit_target_scope_key(target: str) -> str:
    t = (target or "").strip()
    if not t:
        return ""
    h = hashlib.sha256(t.encode("utf-8", errors="ignore")).hexdigest()[:32]
    sk = f"t_{h}"
    return sk if _SCOPE_KEY_RE.match(sk) else ""


def rate_limit_workspace_scope_key(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    for key, prefix in (
        ("workspace_id", "w"),
        ("_workspace_id", "w"),
        ("tenant_id", "w"),
        ("_tenant_id", "w"),
        ("project_id", "p"),
        ("_project_id", "p"),
    ):
        raw = context.get(key)
        if not isinstance(raw, str):
            continue
        seg = re.sub(r"[^a-zA-Z0-9._-]", "_", raw.strip())[:120]
        if not seg:
            continue
        sk = f"{prefix}_{seg}"
        if _SCOPE_KEY_RE.match(sk):
            return sk
    return ""


def dispatch_rate_limit_exceeded_response(*, error_code: str, limit: int, scope_hint: str) -> ExecuteSkillResponse:
    return ExecuteSkillResponse(
        status="FAILED",
        parsed_artifacts={
            "error": error_code,
            "message": f"dispatch rate limit exceeded ({error_code})",
            "limit": limit,
            "scope_hint": scope_hint[:256],
        },
        raw_stdout=None,
        raw_stderr=None,
        duration_ms=0,
    )


async def acquire_dispatch_rate_limits(
    store: TaskStore,
    *,
    target: str,
    context: dict[str, Any] | None,
) -> tuple[bool, list[Callable[[], Awaitable[None]]]]:
    """
    按配置尝试占用 target / workspace 槽。成功返回 (True, release_fns)；失败时已回滚已占槽，返回 (False, [])。
    """
    pending: list[Callable[[], Awaitable[None]]] = []

    async def _rollback() -> None:
        for fn in reversed(pending):
            try:
                await fn()
            except Exception:
                logger.debug("dispatch_rate_limit rollback release failed", exc_info=True)

    tlim = dispatch_max_inflight_per_target()
    if tlim > 0:
        tk = rate_limit_target_scope_key(target)
        if tk:
            ok = await store.try_acquire_rate_slot(tk, tlim)
            if not ok:
                await _rollback()
                return False, []
            sk_final = tk

            async def _rel_t() -> None:
                await store.release_rate_slot(sk_final)

            pending.append(_rel_t)

    wlim = dispatch_max_inflight_per_workspace()
    if wlim > 0:
        wk = rate_limit_workspace_scope_key(context)
        if wk:
            ok = await store.try_acquire_rate_slot(wk, wlim)
            if not ok:
                await _rollback()
                return False, []
            wk_final = wk

            async def _rel_w() -> None:
                await store.release_rate_slot(wk_final)

            pending.append(_rel_w)

    return True, pending


async def release_dispatch_rate_limits(release_fns: list[Callable[[], Awaitable[None]]]) -> None:
    for fn in reversed(release_fns):
        try:
            await fn()
        except Exception:
            logger.debug("dispatch_rate_limit release failed", exc_info=True)
