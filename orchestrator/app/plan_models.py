from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, conint, constr, field_validator


class PlanSchemaVersion(str, Enum):
    """
    统一的 Plan / Compile 相关结构版本。

    注意：
    - 仅在破坏性变更（字段兼容性断裂）时才新增枚举值；
    - 读写双方都必须显式处理未知版本（通常拒绝或降级为 legacy 路径）。
    - plan-v1 是当前唯一对外版本；PlanList 级 kit_id/tactical_goal 已作为 v1 内置字段保留。
    """

    V1 = "plan-v1"


class PlanConstraints(BaseModel):
    """
    每条计划项的约束集合。

    与文档 §4.3 中的约束对应：
    - target_scope：约束目标范围（host / CIDR / 协议等的高层语义）；
    - timeout_seconds：单条指令在执行层的超时时间上限（由编译器/执行器进一步裁剪）；
    - max_parallelism：该计划在同一 task/plan_id 下允许的最大并行度提示（实际调度由 R6 执行）。
    """

    schema_version: PlanSchemaVersion = Field(
        default=PlanSchemaVersion.V1,
        description="约束结构版本；仅在破坏性变更时才升级。",
    )
    target_scope: str = Field(
        ...,
        description="目标范围约束的规范化描述（例如 host/CIDR 标签或内部定义的 scope 名称）。",
        min_length=1,
    )
    timeout_seconds: conint(gt=0, le=3600) = Field(
        ...,
        description="单条指令允许的最大执行时间（秒），由编译器/执行器在此基础上裁剪。",
    )
    max_parallelism: conint(ge=1, le=1024) | None = Field(
        default=None,
        description="可选：Planner 建议的并行度（契约预留）。当前运行时仅以派发层 `MAX_IN_FLIGHT_SKILLS_PER_TASK`（默认 1）+ TaskStore 在途计数为硬上限；尚未与本字段做 min 联动。",
    )


class ContextChunkRef(BaseModel):
    """
    计划引用的上下文 Chunk 指针。

    仅承载指针信息，真正的上下文内容由 Chunk Store 提供。
    """

    schema_version: PlanSchemaVersion = Field(
        default=PlanSchemaVersion.V1,
        description="指针结构版本；与 Plan/Compiler 主版本保持一致。",
    )
    chunk_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="指向 Chunk 存储中某一块上下文的唯一 id。",
    )
    # 可选：未来扩展为 multi-tenant/ACL 过滤时使用的 hint 字段
    tenant_id: Optional[str] = Field(
        default=None,
        description="可选：显式租户/workspace 标识，便于编译器做 ACL 校验。",
    )


class ResolvedContextChunk(BaseModel):
    """kb-r5b：InstructionCompiler 从 Chunk Store 拉取并通过 ACL 后的正文（与 ref 同序）。"""

    model_config = ConfigDict(extra="ignore")

    chunk_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="与 ContextChunkRef.chunk_id 一致。",
    )
    chunk_type: str = Field(default="", max_length=256, description="来自 chunk meta.chunk_type")
    body: Any = Field(default=None, description="content.json 根 JSON 值（多为 object）。")


class PlanItem(BaseModel):
    """
    单条计划项契约。

    必含字段见文档 §4.3：
    - plan_id：本计划在任务内的唯一标识；
    - skill_id：最终将映射到执行器可执行的 skill；
    - plan_content：自然语言或半结构化的意图说明；
    - context_chunk_refs：引用的上下文块列表；
    - constraints：至少包含 scope 与 timeout 语义。
    """

    schema_version: PlanSchemaVersion = Field(
        default=PlanSchemaVersion.V1,
        description="PlanItem 结构版本；跨服务传输时必须保留。",
    )
    plan_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="计划项在单个任务内的唯一 id（非全局）。",
    )
    task_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="所属任务 id，便于下游在无外部上下文时仍可进行 ACL 校验。",
    )
    skill_id: str = Field(
        default="",
        max_length=256,
        description=(
            "计划目标对应的业务 skill 标识；可为空字符串：在声明 kit_id（或继承 PlanList.kit_id）且"
            " 开启 ORCH_PLAN_KIT_ANCHOR_SKILL 时，由编排器按 Kit 注册表顺序在当相可用列表中物化首个成员。"
        ),
    )

    @field_validator("skill_id", mode="before")
    @classmethod
    def _coerce_skill_id(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()
    kit_id: Optional[str] = Field(
        default=None,
        max_length=160,
        description="可选：能力包 id（如 web-recon-v1）；声明时将可用技能收窄为 Kit 成员 ∩ 阶段可用列表。",
    )
    tactical_goal: Optional[str] = Field(
        default=None,
        max_length=8192,
        description="可选：相对 plan_content 更短的战术目标句；注入战术前缀 [Tactical Goal]。",
    )
    plan_content: str = Field(
        ...,
        description="对本计划意图的自然语言或半结构化描述，用于审核与 KB 特征抽取。",
        min_length=1,
    )
    context_chunk_refs: List[ContextChunkRef] = Field(
        default_factory=list,
        description="编排侧引用的上下文 Chunk 指针列表；可为空但需在编译/执行时受策略约束。",
    )
    constraints: PlanConstraints = Field(
        ...,
        description="本计划项的约束集合（scope/timeout/max_parallelism 等）。",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="可选：与 UI 或审计相关的附加元数据（例如创建来源、推荐理由等）。",
    )


class PlanOrchestrationHints(BaseModel):
    """Planner 对编排器元动作的可选提示（阶段推进等），不影响单条 PlanItem 的执行语义。"""

    model_config = ConfigDict(extra="ignore")

    advance_phase: bool = Field(
        default=False,
        description="为 true 且提供 next_phase 时，校验通过后由状态机尝试进入该阶段。",
    )
    next_phase: Optional[str] = Field(
        default=None,
        description="目标阶段枚举名（如 THREAT_MODEL、VULN_SCAN），经 coerce_phase_from_llm 解析。",
    )
    reason: Optional[str] = Field(default=None, description="给人看的推进理由，写入 trace/history。")


class PlanList(BaseModel):
    """
    一次决策产生的计划列表。

    用于在 Planner/Compiler 之间传递结构化计划，而非直接执行参数 JSON。
    """

    schema_version: PlanSchemaVersion = Field(
        default=PlanSchemaVersion.V1,
        description="PlanList 结构版本；v1 可带顶层 kit/tactical 缺省。",
    )
    task_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="所属任务 id。",
    )
    kit_id: Optional[str] = Field(
        default=None,
        max_length=160,
        description="可选：能力包缺省 id；当各 PlanItem 未声明 kit_id 时由业务校验与派发层继承。",
    )
    tactical_goal: Optional[str] = Field(
        default=None,
        max_length=8192,
        description="可选：战术目标缺省句；item 未声明 tactical_goal 时由派发层继承注入 [Tactical Goal]。",
    )
    # 可选：一次 Planner 输出可能有自己的批次 id（例如 plan-v1-<uuid>）
    batch_id: Optional[str] = Field(
        default=None,
        description="可选：本次 Planner 输出的批次标识，用于追踪 plan/compile/execution 轨迹。",
    )
    items: List[PlanItem] = Field(
        default_factory=list,
        description="具体计划项列表。",
    )
    orchestration: Optional[PlanOrchestrationHints] = Field(
        default=None,
        description="可选：阶段推进等编排提示；缺省时不改变当前阶段。",
    )


class CompiledInstruction(BaseModel):
    """
    执行器侧 InstructionCompiler 的输出契约。

    将 PlanItem + Chunk + Skill 元数据编译为可直接交给 Executor/Worker 执行的指令。
    """

    schema_version: PlanSchemaVersion = Field(
        default=PlanSchemaVersion.V1,
        description="CompiledInstruction 结构版本；与 PlanItem/PlanList 同步演进。",
    )
    task_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="所属任务 id。",
    )
    plan_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="来源 PlanItem 的 id，便于回溯与审计。",
    )
    skill_id: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="最终将由 Executor 执行的 skill 标识。",
    )
    target: str | HttpUrl = Field(
        ...,
        description="经 scope 校验与归一化后的具体执行目标（URL/host:port 等）。",
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="将传递给对应 skill 的参数字典，键需受白名单约束（由 Skill 元数据定义）。",
    )
    timeout_seconds: conint(gt=0, le=3600) = Field(
        ...,
        description="单次执行允许的最大时间，通常由 PlanConstraints 与 skill 上限共同裁剪而得。",
    )
    context_chunk_refs: List[ContextChunkRef] = Field(
        default_factory=list,
        description="编译后仍需携带的上下文 Chunk 引用列表。",
    )
    resolved_context_chunks: List[ResolvedContextChunk] = Field(
        default_factory=list,
        description="kb-r5b：已拉取的 chunk 正文；与 context_chunk_refs 顺序一致；关闭 ORCH_COMPILER_VERIFY_CHUNKS 时为空。",
    )


class PlanErrorCode(str, Enum):
    """
    计划/编译相关的标准错误码枚举。

    仅定义与 Plan/Compiler 直接相关的错误；执行器运行时错误仍由现有错误通道承载。
    """

    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    INVALID_PLAN_ITEM = "INVALID_PLAN_ITEM"
    INVALID_PLAN_LIST = "INVALID_PLAN_LIST"
    COMPILATION_FAILED = "COMPILATION_FAILED"
    CHUNK_NOT_FOUND = "CHUNK_NOT_FOUND"
    CHUNK_FORBIDDEN = "CHUNK_FORBIDDEN"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"


class PlanErrorEnvelope(BaseModel):
    """
    计划/编译失败时返回的结构化错误包。

    与文档中的「错误包 Pydantic 或 JSON Schema」要求对应：
    - code：稳定错误码，供编排器与客户端自动处理或重试；
    - message：给人看的简短说明；
    - details：可机读的附加信息（字段缺失列表、schema_version 等）。
    """

    schema_version: PlanSchemaVersion = Field(
        default=PlanSchemaVersion.V1,
        description="错误包结构版本；随 Plan 契约一起演进。",
    )
    code: PlanErrorCode = Field(
        ...,
        description="稳定的错误码，供上游与客户端解析。",
    )
    message: str = Field(
        ...,
        description="给人阅读的简短错误说明（不应包含敏感信息）。",
        min_length=1,
        max_length=512,
    )
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="可选的结构化细节，例如字段错误列表、原始 schema_version 等。",
    )
