"""
V1 Agent 调度策略接口（旁路模块，不接入主 state_machine）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from app.core.v1_agent_registry import AgentSpec

_DEFAULT_PHASE_CAPABILITY = {
    "RECON": "recon",
    "THREAT_MODEL": "threat-model",
    "VULN_SCAN": "vuln-scan",
    "EXPLOIT": "exploit",
    "REPORT": "report",
    "BYPASS": "bypass",
}

# phase → AgentSpec.role（小写）；仅当 V1_SCHEDULING_ROLE_FILTER_ENABLED 开启时用于收窄候选
_DEFAULT_PHASE_ROLE = {
    "RECON": "recon",
    "THREAT_MODEL": "recon",
    "VULN_SCAN": "recon",
    "EXPLOIT": "exploit",
    "REPORT": "report",
    "BYPASS": "bypass",
}


@dataclass(frozen=True)
class SchedulingRequest:
    phase: str
    task_id: str
    preferred_capability: str | None = None


class V1SchedulingPolicy(Protocol):
    def choose(self, request: SchedulingRequest, agents: list[AgentSpec]) -> list[AgentSpec]:
        ...


def _target_capability(request: SchedulingRequest) -> str:
    if request.preferred_capability and request.preferred_capability.strip():
        return request.preferred_capability.strip()
    return _DEFAULT_PHASE_CAPABILITY.get((request.phase or "").strip().upper(), "").strip()


def scheduling_role_filter_enabled() -> bool:
    """默认 false，与现网 capability-first 行为完全一致。"""
    raw = (os.getenv("V1_SCHEDULING_ROLE_FILTER_ENABLED") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _target_role_for_phase(phase: str) -> str | None:
    r = _DEFAULT_PHASE_ROLE.get((phase or "").strip().upper(), "")
    return r.strip().lower() or None


def _apply_role_narrowing(candidates: list[AgentSpec], phase: str) -> list[AgentSpec]:
    if not scheduling_role_filter_enabled():
        return candidates
    want = _target_role_for_phase(phase)
    if not want:
        return candidates
    narrowed = [a for a in candidates if (a.role or "").strip().lower() == want]
    return narrowed or candidates


class CapabilityFirstPolicy:
    """
    先按 capability 过滤，再回退全部 enabled agent。
    """

    def choose(self, request: SchedulingRequest, agents: list[AgentSpec]) -> list[AgentSpec]:
        enabled = [a for a in agents if a.enabled]
        cap = _target_capability(request)
        if not cap:
            pool = enabled
        else:
            matched = [a for a in enabled if cap in a.capabilities]
            pool = matched or enabled
        return _apply_role_narrowing(pool, request.phase)


class RoundRobinPolicy:
    """
    在 capability-first 的候选集基础上做 task 级稳定轮转。
    """

    def __init__(self) -> None:
        self._base = CapabilityFirstPolicy()

    def choose(self, request: SchedulingRequest, agents: list[AgentSpec]) -> list[AgentSpec]:
        candidates = self._base.choose(request, agents)
        if len(candidates) <= 1:
            return candidates
        idx = abs(hash(request.task_id or "")) % len(candidates)
        return candidates[idx:] + candidates[:idx]


def build_v1_scheduling_policy_from_env() -> V1SchedulingPolicy:
    """
    V1_SCHEDULING_POLICY:
    - capability_first（默认）
    - round_robin
    """
    mode = (os.getenv("V1_SCHEDULING_POLICY") or "capability_first").strip().lower()
    if mode == "round_robin":
        return RoundRobinPolicy()
    return CapabilityFirstPolicy()
