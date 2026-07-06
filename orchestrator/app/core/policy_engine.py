"""
技能预检策略引擎：按 skill_id 分发到独立 handler，编排器核心不嵌入具体工具分支。

扩展新工具：在 _PREFLIGHT_HANDLERS 注册即可（或未来由配置声明 handler 名）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.preflight_context import PreflightContext
from app.core.preflight_dirsearch import apply_dirsearch_preflight
from app.core.preflight_dispatcher import apply_dispatcher_preflight
from app.core.preflight_nuclei import apply_nuclei_preflight


class PolicyEngine:
    """表驱动：skill_id -> 预检函数（就地修改 params 与 pctx.ctx）。"""

    _HANDLERS: dict[str, Callable[[dict[str, Any], PreflightContext], None]] = {
        "dispatcher": apply_dispatcher_preflight,
        "dirsearch": apply_dirsearch_preflight,
        "nuclei": apply_nuclei_preflight,
    }

    @classmethod
    def apply(cls, skill_id: str, params: dict[str, Any], pctx: PreflightContext) -> None:
        sid = (skill_id or "").strip().lower()
        fn = cls._HANDLERS.get(sid)
        if fn is not None:
            fn(params, pctx)
