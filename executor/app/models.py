from typing import Any, Dict, Literal, Self

from pydantic import BaseModel, Field, model_validator


class SkillRequest(BaseModel):
    task_id: str
    skill_id: str
    target: str
    params: Dict[str, Any] = {}
    # 为后续更多上下文预留
    context: Dict[str, Any] = {}
    # 红队安全：编排器下发的授权目标，执行器仅允许对该目标或同主机执行，禁止内网扩大化
    allowed_target: str | None = None
    # 可选：MQ 或编排器下发的请求唯一 ID，用于幂等与去重（写 Evidence 时携带）
    request_id: str | None = None
    # 顶层路由：目前仅支持工具 Skill。缺省由 execution_kind.resolve_execution_kind 归一为 skill。
    execution_kind: Literal["skill"] | None = Field(default=None)
    # 内部标记：execution_kind 是否为请求显式提供（非 skill_id 缺省推导），用于 EXECUTOR_REQUIRE_EXECUTION_KIND 门禁
    execution_kind_explicit: bool = Field(default=False)

    @model_validator(mode="after")
    def _normalize_execution_kind(self) -> Self:
        from app.execution_kind import resolve_execution_kind

        was_explicit = self.execution_kind is not None
        resolved = resolve_execution_kind(skill_id=self.skill_id, execution_kind=self.execution_kind)
        object.__setattr__(self, "execution_kind", resolved)
        object.__setattr__(self, "execution_kind_explicit", was_explicit)
        return self


class SkillResult(BaseModel):
    status: str  # SUCCESS | FAILED | TIMEOUT
    parsed_artifacts: Dict[str, Any] | None = None
    raw_stdout: str | None = None
    raw_stderr: str | None = None
    duration_ms: int | None = None
    #: MQ / Daemon 旁路：stderr S-06 嗅探得到的工件 ref 有序列表（与 Redis `artifact_refs_v1` 对齐）
    artifact_refs_v1: list[str] | None = None
    #: 可选：LLM token usage（编排器 FinOps 累计），见 parsed_artifacts.usage
    usage: Dict[str, Any] | None = None
    #: 增量工件解析结果；编排器发 evidence `INCREMENTAL_ARTIFACT` trace
    incremental_artifacts: list[dict[str, Any]] | None = None
