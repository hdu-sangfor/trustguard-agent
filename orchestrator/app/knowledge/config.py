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
    endpoint: str
    access_token: str | None
    scope: KnowledgeScope
    mode: RetrievalMode
    limit: int
    rewrite: bool
    timeout_seconds: float
    max_retries: int
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
        raw_scope = (env.get("KNOWLEDGE_MCP_SCOPE") or "penetration").strip()
        raw_mode = (env.get("KNOWLEDGE_MCP_MODE") or "comprehensive").strip()
        return cls(
            enabled=_bool_env(env, "KNOWLEDGE_MCP_ENABLED", False),
            shadow_mode=_bool_env(env, "KNOWLEDGE_MCP_SHADOW_MODE", True),
            endpoint=endpoint,
            access_token=(env.get("KNOWLEDGE_MCP_ACCESS_TOKEN") or "").strip() or None,
            scope=KnowledgeScope(raw_scope),
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
            workspace_id=(env.get("KNOWLEDGE_MCP_WORKSPACE_ID") or "default").strip()
            or "default",
            workflow_type=(
                env.get("KNOWLEDGE_MCP_WORKFLOW_TYPE") or "penetration"
            ).strip()
            or "penetration",
        )


def knowledge_mcp_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return _bool_env(env, "KNOWLEDGE_MCP_ENABLED", False)
