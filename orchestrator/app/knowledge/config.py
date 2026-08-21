from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.knowledge.models import KnowledgeScope, RetrievalMode


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = (env.get(name) or ("true" if default else "false")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _int_env(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int((env.get(name) or str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _float_env(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float((env.get(name) or str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class KnowledgeMcpSettings:
    enabled: bool
    shadow_mode: bool
    materialize_enabled: bool
    inject_enabled: bool
    endpoint: str
    access_token: str | None
    penetration_scope: KnowledgeScope
    alert_triage_scope: KnowledgeScope
    mode: RetrievalMode
    limit: int
    rewrite: bool
    timeout_seconds: float
    max_retries: int
    materialize_limit: int
    workspace_id: str
    workflow_type: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> KnowledgeMcpSettings:
        env = environ if environ is not None else os.environ
        endpoint = (
            env.get("KNOWLEDGE_MCP_URL") or "http://localhost:18201/mcp"
        ).strip()
        if not endpoint:
            endpoint = "http://localhost:18201/mcp"
        legacy_scope = (env.get("KNOWLEDGE_MCP_SCOPE") or "").strip()
        raw_penetration_scope = (
            env.get("KNOWLEDGE_MCP_PENETRATION_SCOPE")
            or legacy_scope
            or "penetration"
        ).strip()
        raw_alert_triage_scope = (
            env.get("KNOWLEDGE_MCP_ALERT_TRIAGE_SCOPE") or "alert-triage"
        ).strip()
        raw_mode = (env.get("KNOWLEDGE_MCP_MODE") or "comprehensive").strip()
        inject_enabled = _bool_env(env, "KNOWLEDGE_MCP_INJECT_ENABLED", False)
        materialize_enabled = _bool_env(
            env,
            "KNOWLEDGE_MCP_MATERIALIZE_ENABLED",
            False,
        ) or inject_enabled
        return cls(
            enabled=_bool_env(env, "KNOWLEDGE_MCP_ENABLED", False),
            shadow_mode=_bool_env(env, "KNOWLEDGE_MCP_SHADOW_MODE", True),
            materialize_enabled=materialize_enabled,
            inject_enabled=inject_enabled,
            endpoint=endpoint,
            access_token=(env.get("KNOWLEDGE_MCP_ACCESS_TOKEN") or "").strip() or None,
            penetration_scope=KnowledgeScope(raw_penetration_scope),
            alert_triage_scope=KnowledgeScope(raw_alert_triage_scope),
            mode=RetrievalMode(raw_mode),
            limit=_int_env(env, "KNOWLEDGE_MCP_LIMIT", 5, minimum=1, maximum=20),
            rewrite=_bool_env(env, "KNOWLEDGE_MCP_REWRITE", False),
            timeout_seconds=_float_env(
                env,
                "KNOWLEDGE_MCP_TIMEOUT_SECONDS",
                8.0,
                minimum=0.5,
                maximum=120.0,
            ),
            max_retries=_int_env(
                env,
                "KNOWLEDGE_MCP_MAX_RETRIES",
                1,
                minimum=0,
                maximum=3,
            ),
            materialize_limit=_int_env(
                env,
                "KNOWLEDGE_MCP_MATERIALIZE_LIMIT",
                3,
                minimum=1,
                maximum=20,
            ),
            workspace_id=(env.get("KNOWLEDGE_MCP_WORKSPACE_ID") or "default").strip()
            or "default",
            workflow_type=(
                env.get("KNOWLEDGE_MCP_WORKFLOW_TYPE") or "penetration"
            ).strip()
            or "penetration",
        )

    @property
    def effective_inject_enabled(self) -> bool:
        return self.inject_enabled and not self.shadow_mode

    @property
    def scope(self) -> KnowledgeScope:
        """Backward-compatible alias for the penetration Workflow scope."""
        return self.penetration_scope


def knowledge_mcp_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return _bool_env(env, "KNOWLEDGE_MCP_ENABLED", False)
