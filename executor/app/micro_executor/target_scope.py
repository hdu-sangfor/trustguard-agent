"""
目标范围校验（S-02）：Agent 传入的 target 须与编排器下发的 allowed_target **同主机语义**。
算法与 `app.main._is_target_allowed` 对齐，供 run.py 在 docker 启动前二次阻断，防 LLM 幻觉越权。
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

# 与 main._is_target_allowed 保持一致
_SEARCH_SKILL_IDS = frozenset({"baidu-search", "web_search", "search"})


class TargetScopeError(ValueError):
    """target 超出 allowed_target 主机范围时抛出。"""


def _default_localhost_alias() -> str:
    return (os.getenv("EXECUTOR_LOCALHOST_ALIAS") or "host.docker.internal").strip()


def host_of(url_or_host: str) -> str:
    """从 URL 或 host:port 提取 host（与 `app.main._host_of` 同逻辑）。"""
    s = (url_or_host or "").strip()
    if not s:
        return ""
    if "://" in s:
        try:
            return urlparse(s).hostname or s.split("/")[0].split(":")[0] or ""
        except Exception:
            return s.split("/")[0].split(":")[0] or ""
    return s.split(":")[0].split("/")[0] or ""


def normalize_scope_host(host: str, *, localhost_alias: str | None = None) -> str:
    alias = (localhost_alias if localhost_alias is not None else _default_localhost_alias()).strip()
    h = (host or "").strip().lower()
    if not h:
        return ""
    if h in ("127.0.0.1", "localhost", "::1"):
        return alias.lower() if alias else "host.docker.internal"
    return h


class TargetScopeValidator:
    """run.py 侧显式调用；禁止手写一套宽松 if。"""

    @staticmethod
    def is_allowed(
        agent_target: str,
        allowed_target: str | None,
        *,
        skill_id: str = "",
        localhost_alias: str | None = None,
    ) -> bool:
        if not allowed_target or not str(allowed_target).strip():
            return True
        if (skill_id or "").lower() in _SEARCH_SKILL_IDS:
            return True
        a = normalize_scope_host(host_of(agent_target), localhost_alias=localhost_alias)
        b = normalize_scope_host(host_of(allowed_target), localhost_alias=localhost_alias)
        if not a or not b:
            return False
        return a == b

    @staticmethod
    def validate(
        agent_target: str,
        allowed_target: str | None,
        *,
        skill_id: str = "",
        localhost_alias: str | None = None,
    ) -> None:
        if TargetScopeValidator.is_allowed(
            agent_target,
            allowed_target,
            skill_id=skill_id,
            localhost_alias=localhost_alias,
        ):
            return
        raise TargetScopeError(
            f"target scope violation: agent target host {host_of(agent_target)!r} "
            f"not allowed by allowed_target host {host_of(allowed_target or '')!r}"
        )
