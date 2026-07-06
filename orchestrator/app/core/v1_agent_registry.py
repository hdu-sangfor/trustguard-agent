"""
V1 Agent 注册表（编排器侧只读能力模块）。

设计目标：
- 不改动主 state_machine，仅提供多 Agent 能力登记与查询基础设施。
- 后续可由调度层按 capability 进行候选过滤。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable


def _parse_v1_agent_score_map_from_env() -> dict[str, float]:
    """
    解析 V1_AGENT_SCORE_MAP：逗号分隔的 agent_id:score，score 为浮点数。
    仅用于只读观测摘要；不参与调度决策（后续可由调度层消费）。
    """
    raw = (os.getenv("V1_AGENT_SCORE_MAP") or "").strip()
    if not raw:
        return {}
    out: dict[str, float] = {}
    for item in raw.split(","):
        seg = (item or "").strip()
        if not seg or ":" not in seg:
            continue
        agent_id, score_raw = seg.split(":", 1)
        agent_id = agent_id.strip()
        score_raw = (score_raw or "").strip()
        if not agent_id:
            continue
        try:
            out[agent_id] = float(score_raw)
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    sandbox: str = "container"

    @staticmethod
    def from_values(
        agent_id: str,
        role: str,
        capabilities: Iterable[str] | None = None,
        enabled: bool = True,
        sandbox: str = "container",
    ) -> "AgentSpec":
        caps = tuple(sorted({(c or "").strip() for c in (capabilities or []) if (c or "").strip()}))
        return AgentSpec(
            agent_id=(agent_id or "").strip(),
            role=(role or "").strip(),
            capabilities=caps,
            enabled=bool(enabled),
            sandbox=(sandbox or "container").strip() or "container",
        )


class V1AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> None:
        if not spec.agent_id:
            raise ValueError("agent_id is required")
        if not spec.role:
            raise ValueError("role is required")
        self._agents[spec.agent_id] = spec

    def list_agents(self, enabled_only: bool = True) -> list[AgentSpec]:
        agents = list(self._agents.values())
        if enabled_only:
            agents = [a for a in agents if a.enabled]
        return sorted(agents, key=lambda a: a.agent_id)

    def find_by_capability(self, capability: str, enabled_only: bool = True) -> list[AgentSpec]:
        cap = (capability or "").strip()
        if not cap:
            return []
        return [a for a in self.list_agents(enabled_only=enabled_only) if cap in a.capabilities]

    def summary(self) -> dict[str, object]:
        enabled = self.list_agents(enabled_only=True)
        enabled_ids = frozenset(a.agent_id for a in enabled)
        raw_scores = _parse_v1_agent_score_map_from_env()
        capability_scores = {aid: raw_scores[aid] for aid in enabled_ids if aid in raw_scores}
        return {
            "total": len(self._agents),
            "enabled": len(enabled),
            "agent_ids": [a.agent_id for a in enabled],
            "capability_scores": capability_scores,
            "scores_source": "env" if raw_scores else "none",
        }


def build_registry_from_env() -> V1AgentRegistry:
    """
    通过环境变量构建只读注册表快照。
    格式：V1_AGENT_REGISTRY="agent_id:role:cap1|cap2:enabled:sandbox,agent2:role:cap:disabled:container"
    - enabled 可用值：enabled/true/1/on，其余视为 false。
    - 缺省 sandbox 时默认 container。
    """
    raw = (os.getenv("V1_AGENT_REGISTRY") or "").strip()
    reg = V1AgentRegistry()
    if not raw:
        return reg
    for item in raw.split(","):
        seg = (item or "").strip()
        if not seg:
            continue
        parts = [p.strip() for p in seg.split(":")]
        if len(parts) < 3:
            continue
        agent_id, role, caps_raw = parts[0], parts[1], parts[2]
        enabled_raw = (parts[3] if len(parts) > 3 else "enabled").strip().lower()
        sandbox = (parts[4] if len(parts) > 4 else "container").strip() or "container"
        caps = [c.strip() for c in caps_raw.split("|") if c.strip()]
        enabled = enabled_raw in {"enabled", "true", "1", "on"}
        try:
            reg.register(AgentSpec.from_values(agent_id, role, caps, enabled=enabled, sandbox=sandbox))
        except ValueError:
            continue
    return reg
