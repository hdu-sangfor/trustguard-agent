from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List

from app.clients.llm_client import call_decision_engine
from app.core.governance_cost import accumulate_finops_usage_mapping_into_state
from app.core.agent_execution import AgentExecution, AgentState, AgentStep, AgentStepState
from app.core.agent_tools import SkillCallContext, SkillExecutor
from app.core.decision_policy import bias_available_skills_for_decision, ensure_baidu_search_offered
from app.core.trajectory_recorder import record_execution
from app.models import LLMDecisionResponse, TaskState


class BasePentestAgent(ABC):
    """
    编排器侧基础 Agent 抽象。

    设计目标：
    - 将「LLM 决策 →（可选）技能执行 → Trace/上下文更新」抽象为可重用骨架；
    - 对上只暴露 `run_single_step` 等接口，保持 `TaskState` 作为状态来源；
    - 为后续 ManagerAgent / 多 Agent 扩展提供统一父类。

    当前阶段，该抽象仅在内部使用，不改变 REST 接口与存储结构。
    """

    def __init__(self, state: TaskState, skill_executor: SkillExecutor | None = None):
        self.state = state
        self.skill_executor = skill_executor or SkillExecutor()

    async def run_single_step(
        self,
        available_skill_ids: List[str],
        *,
        enable_executor: bool,
    ) -> AgentExecution:
        """
        执行一次单步决策（对应一次 orchestrator tick），返回轻量轨迹。

        重要：当前实现不直接修改外层 TaskState 的阶段/终态迁移逻辑，
        这些仍交由现有 state_machine 处理；该方法主要抽象「决策与技能执行」部分，
        后续在 migrate-state-machine 步骤中再整体迁移。
        """
        execution = AgentExecution(task_id=self.state.task_id, final_state=AgentState.RUNNING)
        step = AgentStep(
            step_number=1,
            state=AgentStepState.THINKING,
            phase=self.state.current_phase.value if self.state.current_phase else None,
            history_summary_before=self.state.history_summary,
        )

        # 1. 调用决策引擎
        decision = await self._call_decision_engine(available_skill_ids)
        step.state = AgentStepState.DECIDED
        step.decision_raw = decision.model_dump()

        # 2. 根据决策类型由具体子类处理（例如：EXECUTE_SKILL / NEXT_PHASE / FINISH）
        try:
            await self._handle_decision(
                decision=decision,
                step=step,
                execution=execution,
                available_skill_ids=available_skill_ids,
                enable_executor=enable_executor,
            )
            step.state = AgentStepState.COMPLETED
            execution.final_state = AgentState.COMPLETED
        except Exception as exc:  # noqa: BLE001
            step.state = AgentStepState.ERROR
            step.error = str(exc)
            execution.error = str(exc)
            execution.final_state = AgentState.ERROR

        step.history_summary_after = self.state.history_summary
        execution.append_step(step)

        # 轻量轨迹落盘（用于调试与后续报告生成，不影响主流程）
        record_execution(execution, phase=self.state.current_phase.value if self.state.current_phase else None)

        return execution

    async def _call_decision_engine(self, available_skill_ids: List[str]) -> LLMDecisionResponse:
        """统一的决策引擎调用封装。"""
        biased_ids = bias_available_skills_for_decision(
            self.state.current_phase,
            available_skill_ids,
            self.state.target_context,
        )
        phase_val = self.state.current_phase.value if self.state.current_phase else ""
        biased_ids = ensure_baidu_search_offered(phase_val, biased_ids)
        decision, llm_usage = await call_decision_engine(
            task_id=self.state.task_id,
            phase=self.state.current_phase,
            target_context=self.state.target_context,
            history_summary=self.state.history_summary,
            available_skill_ids=biased_ids,
        )
        if llm_usage:
            accumulate_finops_usage_mapping_into_state(self.state, llm_usage)
        return decision

    @abstractmethod
    async def _handle_decision(
        self,
        *,
        decision: LLMDecisionResponse,
        step: AgentStep,
        execution: AgentExecution,
        available_skill_ids: List[str],
        enable_executor: bool,
    ) -> None:
        """
        由子类实现的决策处理逻辑。

        在迁移完成前，默认实现可以仅在内部使用，不直接改变 state_machine 的行为；
        在后续 migrate-state-machine 步骤中，我们会让该逻辑完全承载
        EXECUTE_SKILL / NEXT_PHASE / FINISH 的语义。
        """


class SimplePentestAgent(BasePentestAgent):
    """
    与状态机语义对齐的最小实现（单 Agent 形态）。

    当前阶段仅在内部使用，用于为后续重构提供落脚点；
    具体状态迁移仍由 state_machine 负责。
    """

    async def _handle_decision(
        self,
        *,
        decision: LLMDecisionResponse,
        step: AgentStep,
        execution: AgentExecution,
        available_skill_ids: List[str],
        enable_executor: bool,
    ) -> None:
        # EXECUTE_SKILL / EXECUTE_SKILLS 时调用 SkillExecutor（Pydantic 已把单步规范为 EXECUTE_SKILLS+actions，此处取首项做单步）
        if decision.action_type not in ("EXECUTE_SKILL", "EXECUTE_SKILLS"):
            return

        skill_id: str | None = decision.skill_id
        params: dict[str, Any] = dict(decision.params or {})
        if (not skill_id or not params) and getattr(decision, "actions", None) and len(decision.actions) > 0:
            first = decision.actions[0]
            skill_id = getattr(first, "skill_id", None)
            params = dict(getattr(first, "params", None) or {})
        if not skill_id:
            raise ValueError("decision missing skill_id for EXECUTE_SKILL/EXECUTE_SKILLS")

        raw_target = str(params.get("target") or self.state.target)
        target = raw_target

        call_ctx = SkillCallContext(
            task_id=self.state.task_id,
            phase=self.state.current_phase,
            skill_id=skill_id,
            target=target,
            params=params,
            allowed_target=self.state.target,
        )

        step.state = AgentStepState.CALLING_TOOL
        step.skill_id = skill_id
        step.skill_target = target
        step.skill_params = params

        result = await self.skill_executor.execute_skill(
            state=self.state,
            call_ctx=call_ctx,
            enable_executor=enable_executor,
            available_skills=available_skill_ids,
        )

        exec_result = result["exec_result"]
        resolved_artifacts = result["resolved_artifacts"]
        artifact_dir = result["artifact_dir"]

        if exec_result is not None:
            step.executor_status = exec_result.status
            step.executor_duration_ms = exec_result.duration_ms
        step.executor_artifacts = resolved_artifacts
        step.executor_artifact_dir = artifact_dir

