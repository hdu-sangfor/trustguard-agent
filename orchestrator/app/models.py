import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from collections import deque

from pydantic import BaseModel, Field, field_validator, model_validator

from app.enums import Phase, TaskStatus
from app.core.task_store import TaskRecord


def phase_wall_clock_limit_sec_from_env() -> Optional[int]:
    """本阶段墙钟上限（秒）；未配置或关闭时返回 None。"""
    raw = (os.getenv("ORCH_PHASE_WALL_CLOCK_LIMIT_SEC") or "").strip()
    if not raw or raw.lower() in ("0", "off", "false", "no"):
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return 600


def max_plan_rounds_per_phase_from_env() -> int:
    """每阶段最大 Plan 决策轮数硬上限；超限后强行推进到下一阶段或 REPORT。
    默认 10 轮；设为 0 或 off 禁用。"""
    raw = (os.getenv("ORCH_MAX_PLAN_ROUNDS_PER_PHASE") or "").strip()
    if not raw:
        return 10
    if raw.lower() in ("0", "off", "false", "no"):
        return 0
    try:
        return max(1, int(raw))
    except ValueError:
        return 10


def apply_user_injected_context(
    target_context: Dict[str, Any],
    *,
    business_background: str | None,
    extra_user_requirements: str | None,
) -> None:
    """
    将用户提供的业务背景与额外需求写入 target_context，供决策与下游使用。

    安全机制（v1-a247）：经 prompt_injection_guard 做长度截断、危险模式检测与策略处置后写入。
    处置策略由 ORCH_PROMPT_INJECTION_MODE 控制（reject/strip/tag，默认 tag）。
    """
    from app.core.prompt_injection_guard import sanitize_user_field

    bg_result = sanitize_user_field(business_background, field_name="business_background")
    if bg_result.value:
        target_context["business_background"] = bg_result.value

    req_result = sanitize_user_field(extra_user_requirements, field_name="extra_user_requirements")
    if req_result.value:
        target_context["extra_user_requirements"] = req_result.value


class ActionItem(BaseModel):
    """单条执行动作：skill_id + params，用于 EXECUTE_SKILLS 的 actions 列表。"""
    skill_id: str
    params: Dict[str, Any] = Field(default_factory=dict)


class TodoStatusUpdate(BaseModel):
    """可选：由 LLM 建议更新当前 Todo 的状态。"""
    todo_id: Optional[str] = None
    status: str = Field(pattern=r"^(PENDING|RUNNING|DONE|FAILED)$")
    reason: Optional[str] = None


class CreateTaskPayload(BaseModel):
    taskId: str
    name: str
    target: str
    description: str | None = None
    businessBackground: str | None = None
    extraUserRequirements: str | None = None


class OrchestratorTaskStateResponse(BaseModel):
    taskId: str
    currentPhase: str
    status: str


def coerce_llm_fact_sequence(raw: Any) -> List[str]:
    """
    LLM 常把 facts_to_add 写成对象数组；下游记忆层需要 List[str]。
    将 dict/list 等规范为 JSON 字符串或 str(x)，供 Field 校验前收敛。
    """
    if raw is None:
        return []
    items: List[Any] = raw if isinstance(raw, list) else [raw]
    out: List[str] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
            continue
        if isinstance(item, (dict, list)):
            try:
                s = json.dumps(item, ensure_ascii=False)
            except (TypeError, ValueError):
                s = str(item)
            if s.strip():
                out.append(s)
            continue
        s = str(item).strip()
        if s:
            out.append(s)
    return out


class LLMDecisionResponse(BaseModel):
    action_type: str = Field(pattern=r"^(EXECUTE_SKILL|EXECUTE_SKILLS|NEXT_PHASE|FINISH)$")
    actions: List[ActionItem] = Field(default_factory=list)
    next_phase: Optional[str] = None
    reasoning: Optional[str] = None
    # 兼容旧版单步输出，由 model_validator 收敛到 actions
    skill_id: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    # v1 事实管理：支持新增与删除
    facts_to_add: List[str] = Field(default_factory=list)
    facts_to_remove: List[str] = Field(default_factory=list)
    # 兼容旧字段：updated_facts 视为 facts_to_add
    updated_facts: List[str] = Field(default_factory=list)
    # 可选：推动当前 todo 状态流转
    todo_status_update: Optional[TodoStatusUpdate] = None

    @field_validator("facts_to_add", "facts_to_remove", "updated_facts", mode="before")
    @classmethod
    def _coerce_fact_string_lists(cls, v: Any) -> List[str]:
        return coerce_llm_fact_sequence(v)

    @model_validator(mode="before")
    @classmethod
    def _normalize_action_type_token(cls, data: Any) -> Any:
        """
        LLM 常输出大小写/空格/连字符变体；先规范再跑 Field pattern，避免无意义校验失败。
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("action_type")
        if not isinstance(raw, str):
            return data
        t = raw.strip().upper().replace(" ", "_").replace("-", "_")
        while "__" in t:
            t = t.replace("__", "_")
        data = dict(data)
        data["action_type"] = t
        return data

    @model_validator(mode="after")
    def normalize_actions(self) -> "LLMDecisionResponse":
        """
        状态机分支只处理 EXECUTE_SKILLS / NEXT_PHASE / FINISH。
        任意「单工具」语义（EXECUTE_SKILL）必须在入状态机前收敛为 EXECUTE_SKILLS + actions，
        否则会出现「仅写了 actions 却带 EXECUTE_SKILL」导致 500 unsupported action_type。
        """
        if self.action_type != "EXECUTE_SKILL":
            return self
        if not self.actions:
            if not (self.skill_id and str(self.skill_id).strip()):
                raise ValueError(
                    "EXECUTE_SKILL requires top-level skill_id or a non-empty actions[] list; "
                    "use EXECUTE_SKILLS with actions=[{\"skill_id\": \"...\", \"params\": {}}] if unsure."
                )
            self.actions = [
                ActionItem(skill_id=str(self.skill_id).strip(), params=self.params or {})
            ]
        self.action_type = "EXECUTE_SKILLS"
        # 规范输出：收敛为批量语义后，不再保留单步字段，避免下游出现歧义。
        self.skill_id = None
        self.params = None
        return self

    @model_validator(mode="after")
    def normalize_memory_fields(self) -> "LLMDecisionResponse":
        # 兼容旧字段 updated_facts -> facts_to_add
        merged_add = [x for x in (self.facts_to_add or []) if isinstance(x, str) and x.strip()]
        if self.updated_facts:
            merged_add.extend([x for x in self.updated_facts if isinstance(x, str) and x.strip()])
        # stable de-dup
        seen: set[str] = set()
        dedup_add: List[str] = []
        for item in merged_add:
            s = item.strip()
            if s in seen:
                continue
            seen.add(s)
            dedup_add.append(s)
        dedup_remove: List[str] = []
        seen_remove: set[str] = set()
        for item in (self.facts_to_remove or []):
            if not isinstance(item, str):
                continue
            s = item.strip()
            if not s or s in seen_remove:
                continue
            seen_remove.add(s)
            dedup_remove.append(s)
        from app.core.memory_store import (
            sanitize_llm_memory_fact_lines,
            sanitize_llm_memory_remove_lines,
        )

        self.facts_to_add = sanitize_llm_memory_fact_lines(dedup_add)
        self.facts_to_remove = sanitize_llm_memory_remove_lines(dedup_remove)
        self.updated_facts = []
        return self


class ExecuteSkillResponse(BaseModel):
    status: str
    parsed_artifacts: Dict[str, Any] | None = None
    raw_stdout: Optional[str] = None
    raw_stderr: Optional[str] = None
    duration_ms: Optional[int] = None
    request_id: Optional[str] = None  # MQ 模式下 DISPATCHED 时返回，供编排器轮询执行结果
    #: 可选：执行器返回的 token usage（FinOps 累计），见 governance_cost.py
    usage: Dict[str, Any] | None = None
    #: 可选：执行器增量工件，见 agent_tools
    incremental_artifacts: list[dict[str, Any]] | None = None


class TraceEvent(BaseModel):
    task_id: str
    timestamp: str
    event_type: str
    source_module: str
    payload: Dict[str, Any]
    run_started_at: str | None = None
    run_finished_at: str | None = None
    run_duration_ms: int | None = None


class TaskState:
    def __init__(
        self,
        task_id: str,
        name: str,
        target: str,
        description: str | None = None,
        *,
        business_background: str | None = None,
        extra_user_requirements: str | None = None,
    ):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.target = target
        self.business_background = business_background
        self.extra_user_requirements = extra_user_requirements
        self.current_phase: Phase = Phase.RECON
        self.phase_start_at: Optional[datetime] = datetime.utcnow()
        self.current_phase_duration_limit_sec: Optional[int] = phase_wall_clock_limit_sec_from_env()
        self.status: TaskStatus = TaskStatus.PENDING
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.target_context: Dict[str, Any] = {
            "target": target,
            "task_background": f"本任务为经授权的渗透测试，仅对 {target} 进行安全测试，禁止越权。",
        }
        apply_user_injected_context(
            self.target_context,
            business_background=business_background,
            extra_user_requirements=extra_user_requirements,
        )
        self.history_summary: str = ""
        self.stop_requested: bool = False
        self.recent_exec_signatures = deque(maxlen=8)
        self.loop_repeat_count: int = 0
        self.phase_plan_round_count: int = 0
        self.coverage_attempted: list = []  # list[dict] with keys target, skill_id
        self.recent_summary_chunks: list = []  # list[str], 建议 maxlen 20
        self.skill_failures: Dict[str, int] = {}
        self.entity_blackboard: Dict[str, Any] = {"targets": {}}
        self.action_ledger = deque(maxlen=64)  # list[dict]: 高保真动作账本
        self.confirmed_facts: list[str] = []
        # host:port -> baseline hash/status/path
        self.fallback_baseline: Dict[str, Dict[str, Any]] = {}
        # FinOps（任务级累计；持久化经 TaskRecord / Redis）
        self.llm_input_tokens_total: int = 0
        self.llm_output_tokens_total: int = 0
        self.cumulative_cost_usd: float = 0.0

    def is_terminal(self) -> bool:
        return self.status in {TaskStatus.DONE, TaskStatus.FAILED}

    def set_phase(self, new_phase: Phase) -> None:
        """修改运行阶段的唯一入口：阶段变化时重置 phase_start_at 并刷新墙钟上限快照。"""
        if self.current_phase == new_phase:
            return
        self.current_phase = new_phase
        self.phase_start_at = datetime.utcnow()
        self.current_phase_duration_limit_sec = phase_wall_clock_limit_sec_from_env()
        self.phase_plan_round_count = 0
        self.updated_at = datetime.utcnow()

    def force_set_phase(self, new_phase: Phase) -> None:
        """内部强制重置阶段时钟；调用方必须先完成 phase guard 校验。"""
        if self.current_phase == new_phase:
            return
        self.current_phase = new_phase
        self.phase_start_at = datetime.utcnow()
        self.current_phase_duration_limit_sec = phase_wall_clock_limit_sec_from_env()
        self.phase_plan_round_count = 0
        self.updated_at = datetime.utcnow()

    def hydrate_phase_clock(
        self,
        *,
        current_phase: Phase,
        phase_start_at: Optional[datetime],
        current_phase_duration_limit_sec: Optional[int] = None,
    ) -> None:
        """从 TaskRecord / checkpoint 恢复阶段与计时锚点，不触发「新阶段」语义。"""
        self.current_phase = current_phase
        self.phase_start_at = phase_start_at
        if current_phase_duration_limit_sec is not None:
            self.current_phase_duration_limit_sec = current_phase_duration_limit_sec
        else:
            # 持久化侧未存上限时与「新任务」一致：按当前环境重新解析，避免 __init__ 残留与 checkpoint 语义不一致
            self.current_phase_duration_limit_sec = phase_wall_clock_limit_sec_from_env()
        self.updated_at = datetime.utcnow()

    def hydrate_finops_counters(self, record: Optional[TaskRecord]) -> None:
        """从 TaskStore 记录恢复累计 token / 成本（断点续跑）。"""
        if record is None:
            return
        try:
            self.llm_input_tokens_total = max(0, int(getattr(record, "llm_input_tokens_total", 0) or 0))
            self.llm_output_tokens_total = max(0, int(getattr(record, "llm_output_tokens_total", 0) or 0))
            self.cumulative_cost_usd = max(0.0, float(getattr(record, "cumulative_cost_usd", 0.0) or 0.0))
        except (TypeError, ValueError):
            pass

    def to_response(self) -> OrchestratorTaskStateResponse:
        return OrchestratorTaskStateResponse(
            taskId=self.task_id,
            currentPhase=self.current_phase.value,
            status=self.status.value,
        )

    def _failure_key(self, phase: Phase, skill_id: str) -> str:
        return f"{phase.value}:{skill_id}"

    def get_skill_failure_count(self, phase: Phase, skill_id: str) -> int:
        if not skill_id:
            return 0
        key = self._failure_key(phase, skill_id)
        return int(self.skill_failures.get(key, 0))

    def increment_skill_failure(self, phase: Phase, skill_id: str) -> int:
        if not skill_id:
            return 0
        key = self._failure_key(phase, skill_id)
        current = int(self.skill_failures.get(key, 0))
        current += 1
        self.skill_failures[key] = current
        return current

    @classmethod
    def from_task_record(cls, record: TaskRecord, *, target_context: Dict[str, Any] | None = None, history_summary: str | None = None) -> "TaskState":
        """
        从 TaskRecord 构造内存 TaskState 视图。
        target_context / history_summary 由调用方提供（例如从 checkpoint 或 workspace 恢复）。
        """
        state = cls(
            task_id=record.task_id,
            name=record.name,
            target=record.target,
            description=record.description,
            business_background=record.business_background,
            extra_user_requirements=record.extra_user_requirements,
        )
        state.status = record.status
        anchor = record.phase_start_at if record.phase_start_at is not None else datetime.utcnow()
        state.hydrate_phase_clock(
            current_phase=record.current_phase,
            phase_start_at=anchor,
            current_phase_duration_limit_sec=record.current_phase_duration_limit_sec,
        )
        state.created_at = record.created_at
        state.updated_at = record.updated_at
        state.stop_requested = record.stop_requested
        state.coverage_attempted = list(record.coverage_attempted or [])
        base_context = {
            "target": record.target,
            "task_background": f"本任务为经授权的渗透测试，仅对 {record.target} 进行安全测试，禁止越权。",
        }
        if target_context is None:
            merged_context = dict(base_context)
        else:
            merged_context = {**base_context, **target_context}
        if "task_background" not in merged_context:
            merged_context["task_background"] = base_context["task_background"]
        if "target" not in merged_context:
            merged_context["target"] = base_context["target"]
        apply_user_injected_context(
            merged_context,
            business_background=record.business_background,
            extra_user_requirements=record.extra_user_requirements,
        )
        state.target_context = merged_context
        state.history_summary = history_summary or ""
        state.hydrate_finops_counters(record)
        return state

    def to_task_record(self) -> TaskRecord:
        """
        将当前 TaskState 映射为 TaskRecord，用于写入 TaskStore。
        注意：不包含 target_context/history_summary，仅同步元信息与覆盖矩阵。
        """
        return TaskRecord(
            task_id=self.task_id,
            name=self.name,
            target=self.target,
            description=self.description,
            business_background=self.business_background,
            extra_user_requirements=self.extra_user_requirements,
            status=self.status,
            current_phase=self.current_phase,
            phase_start_at=self.phase_start_at,
            current_phase_duration_limit_sec=self.current_phase_duration_limit_sec,
            stop_requested=self.stop_requested,
            coverage_attempted=list(self.coverage_attempted or []),
            created_at=self.created_at,
            updated_at=self.updated_at,
            summary_pointers={},  # 由上下文/摘要模块维护
            llm_input_tokens_total=int(self.llm_input_tokens_total or 0),
            llm_output_tokens_total=int(self.llm_output_tokens_total or 0),
            cumulative_cost_usd=float(self.cumulative_cost_usd or 0.0),
        )
