"""
阶段级能力策略（v1-a233 / execution_kind 演进阶段 B 占位）。

- 不修改 PlanList / PlanItem JSON schema；阶段默认值通过本模块在运行期合并。
- 与 TaskState.set_phase 墙钟快照正交；墙钟仍由 ORCH_PHASE_WALL_CLOCK_LIMIT_SEC 等控制。
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.enums import Phase
from app.core.execution_kind import ExecutionKind, resolve_execution_kind


class CapabilityKit(BaseModel):
    """阶段内 Planner/派发可参考的意图摘要（非可执行 skill 清单）。"""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(
        default="",
        description="给人读的策略摘要；可进入提示词或 trace，不参与编译器硬门禁。",
    )
    default_execution_kind: Optional[Literal["skill"]] = Field(
        default=None,
        description=(
            "当 PlanItem.metadata 未显式给出 execution_kind 时，尝试与 skill_id 合并推导；"
            "若与 skill_id 冲突则忽略该默认（回退到 skill_id 缺省推导）。"
        ),
    )


class PhasePolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phase: str = Field(..., description="与 Phase 枚举 value 一致")
    kit: CapabilityKit = Field(default_factory=CapabilityKit)


def _normalize_metadata_execution_kind(metadata: Any) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get("execution_kind")
    s = str(raw or "").strip().lower().replace("-", "_")
    return s or None


def policy_for_phase(phase: Phase) -> PhasePolicy:
    return PHASE_CAPABILITY_REGISTRY.get(phase, PhasePolicy(phase=phase.value, kit=CapabilityKit()))


def effective_plan_item_execution_kind(
    *,
    skill_id: str,
    metadata: Any,
    current_phase: Phase | None,
) -> ExecutionKind:
    """
    阶段 B 合并顺序：
    1) metadata.execution_kind（skill）
    2) 当前阶段 CapabilityKit.default_execution_kind（与 skill_id 不兼容则跳过）
    3) resolve_execution_kind(skill_id, None)
    """
    meta_ek = _normalize_metadata_execution_kind(metadata)
    if meta_ek is not None:
        return resolve_execution_kind(skill_id=skill_id, execution_kind=meta_ek)

    if current_phase is not None:
        default_ek = policy_for_phase(current_phase).kit.default_execution_kind
        if default_ek is not None:
            try:
                return resolve_execution_kind(skill_id=skill_id, execution_kind=default_ek)
            except ValueError:
                pass

    return resolve_execution_kind(skill_id=skill_id, execution_kind=None)


# 阶段 → 策略表（可随产品迭代调整；默认以 skill 为主）
PHASE_CAPABILITY_REGISTRY: dict[Phase, PhasePolicy] = {
    Phase.RECON: PhasePolicy(
        phase=Phase.RECON.value,
        kit=CapabilityKit(
            summary="Reconnaissance: prefer registered security tools.",
            default_execution_kind="skill",
        ),
    ),
    Phase.THREAT_MODEL: PhasePolicy(
        phase=Phase.THREAT_MODEL.value,
        kit=CapabilityKit(
            summary="Threat modeling: structured analysis; default tool-plane unless agent metadata set.",
            default_execution_kind="skill",
        ),
    ),
    Phase.VULN_SCAN: PhasePolicy(
        phase=Phase.VULN_SCAN.value,
        kit=CapabilityKit(
            summary="Vulnerability scanning: tool-forward phase.",
            default_execution_kind="skill",
        ),
    ),
    Phase.EXPLOIT: PhasePolicy(
        phase=Phase.EXPLOIT.value,
        kit=CapabilityKit(
            summary="Exploitation: high-risk tools; default tool-plane unless explicit agent intent.",
            default_execution_kind="skill",
        ),
    ),
    Phase.REPORT: PhasePolicy(
        phase=Phase.REPORT.value,
        kit=CapabilityKit(
            summary="Reporting phase.",
            default_execution_kind=None,
        ),
    ),
    Phase.DONE: PhasePolicy(
        phase=Phase.DONE.value,
        kit=CapabilityKit(summary="Terminal phase.", default_execution_kind=None),
    ),
}
