from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentStepState(Enum):
    """Agent 单步执行状态。"""

    THINKING = "thinking"
    DECIDED = "decided"
    CALLING_TOOL = "calling_tool"
    COMPLETED = "completed"
    ERROR = "error"


class AgentState(Enum):
    """Agent 整体执行状态。"""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class AgentStep:
    """
    单次「决策 →（可选）技能执行」步骤的轨迹记录结构。

    注意：这是编排器内部用于调试与可观测性的轻量结构，
    不直接暴露给 Gateway API 或存储层。
    """

    step_number: int
    state: AgentStepState

    # 决策相关
    phase: str | None = None
    decision_raw: dict[str, Any] | None = None

    # 技能执行相关（仅在 EXECUTE_SKILL 时有值）
    skill_id: str | None = None
    skill_target: str | None = None
    skill_params: dict[str, Any] | None = None
    executor_status: str | None = None
    executor_duration_ms: int | None = None
    executor_artifacts: dict[str, Any] | None = None
    executor_artifact_dir: str | None = None

    # 诊断与错误
    history_summary_before: str | None = None
    history_summary_after: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentExecution:
    """
    封装一次完整 Agent 执行过程（当前主要用于「单步 tick」的聚合记录）。

    在当前阶段，我们仍由 `TaskState` 作为唯一事实来源；
    本结构更多用于后续调试/可视化与扩展到多 Agent 时的统一接口。
    """

    task_id: str
    steps: list[AgentStep] = field(default_factory=list)
    final_state: AgentState = AgentState.IDLE
    final_result: str | None = None
    error: str | None = None

    def append_step(self, step: AgentStep) -> None:
        self.steps.append(step)

