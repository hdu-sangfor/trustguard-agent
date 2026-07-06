"""
KB-R1a / KB-R1b：Experience 向量点的 payload 契约、租户检索过滤、Qdrant 迁移说明；
以及执行产物 Chunk 化（chunk_id）与「向量仅意图句」嵌入策略。

迁移策略（摘要）：
1. **新写入**：`QdrantKBClient.propose_experience` 仅 upsert 经 `ExperiencePayload` 校验后的 dict；
   建议生产为每条经验写入非空的 `workspace_id`（及可选 `project_id`），与编排 `target_context` 或
   环境变量 `ORCH_KB_DEFAULT_WORKSPACE_ID` / `ORCH_KB_DEFAULT_PROJECT_ID` 对齐。
2. **存量点**：旧 payload 无 `workspace_id` 时，在 `KB_EXPERIENCE_TENANT_FILTER=auto` 下若当前任务解析出
   租户键，Qdrant `must` 条件会 **排掉** 无该字段的点。检索侧 **默认**（`KB_EXPERIENCE_EFFECTIVENESS_SOFT`）不再 must `proven`，
   由 `kb_retrieval_scoring.apply_soft_retrieval_scoring` 加权；需旧行为可设 `KB_EXPERIENCE_EFFECTIVENESS_SOFT=false`。迁移可选：
   - 用 Qdrant `scroll` + `set_payload` 批量补 `workspace_id`/`project_id`；
   - 或临时将 `KB_EXPERIENCE_TENANT_FILTER=off` 直至回填完成；
   - 或迁到新 collection 并双读（见 kb-r1d）。
3. **集合重建**：`KB_AUTO_CREATE=true` 时新建集合的向量维仍由 embed 探测决定；payload 结构演进靠
   `schema_version` 与 Pydantic 校验，不要求删集合。
4. **kb-r1d 双读**：新写入仅进主 `KB_COLLECTION_EXPERIENCE`；旧数据可留在 **`KB_EXPERIENCE_LEGACY_COLLECTION`**，
   检索时两路合并（legacy 路不加 workspace must）。旧点向量质量需 **re-embed** 时另行批处理。

KB-R1b：执行产物先写入 `chunk_store`（`kb_runtime_experience`）得到 `chunk_id` 进入 Qdrant payload；
向量默认只对 `intent_embed_text` 嵌入；`summary` 仍可含较长文本。`KB_EXPERIENCE_EMBED_FULL_SUMMARY=true`
时恢复用完整 summary 做 embed。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.chunk_store import CHUNK_ID_PREFIX


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def kb_experience_effectiveness_soft_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """
    kb-r4a：为 True（默认）时检索 **不** 在 Qdrant must 中限定 effectiveness=proven，
    依赖 `apply_soft_retrieval_scoring` 对 pending/proven/deprecated 加权。
    """
    env = environ if environ is not None else os.environ
    v = (env.get("KB_EXPERIENCE_EFFECTIVENESS_SOFT") or "true").strip().lower()
    return v not in ("0", "false", "no", "off")


def _strip_or_none(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def stable_experience_artifact_chunk_id(artifact_ref: str) -> str:
    """与同一 artifact_ref 幂等对应的 chunk_id（chk- + 24 hex）。"""
    d = hashlib.sha256((artifact_ref or "").encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"{CHUNK_ID_PREFIX}{d}"


def build_intent_embed_text(
    *,
    phase: str,
    skill_id: str,
    target: str,
    headline: str,
    max_len: int = 512,
) -> str:
    """供向量嵌入的短意图句（不含大块 artifacts JSON）。"""
    h = (headline or "").strip().split("\n", 1)[0].strip()
    h = h[:400]
    phase_s = (phase or "").strip()
    skill_s = (skill_id or "").strip()
    tgt = (target or "").strip()[:200]
    parts = [f"phase={phase_s}", f"skill={skill_s}", f"target={tgt}", h]
    s = " | ".join(p for p in parts if p)
    return s[:max_len] if max_len > 0 else s


def pick_workspace_scope_from_context(
    target_context: dict[str, Any] | None,
    *,
    include_env_defaults: bool = True,
) -> tuple[str | None, str | None]:
    """
    从决策上下文解析租户键；可选叠加大租户默认值（单租户集群便于与写入侧一致）。
    """
    ctx = target_context or {}

    def pick(keys: tuple[str, ...]) -> str | None:
        for k in keys:
            got = _strip_or_none(ctx.get(k))
            if got:
                return got
        return None

    workspace_id = pick(("workspace_id", "_workspace_id", "tenant_id", "_tenant_id"))
    project_id = pick(("project_id", "_project_id"))

    if include_env_defaults:
        if not workspace_id:
            workspace_id = _strip_or_none(os.getenv("ORCH_KB_DEFAULT_WORKSPACE_ID"))
        if not project_id:
            project_id = _strip_or_none(os.getenv("ORCH_KB_DEFAULT_PROJECT_ID"))
    return workspace_id, project_id


def build_experience_retrieve_filters(
    *,
    phase: Any,
    target_context: dict[str, Any] | None,
    todo_id: str,
) -> dict[str, Any]:
    """
    经验检索过滤：默认带 phase；effectiveness 仅当 KB_EXPERIENCE_EFFECTIVENESS_SOFT=false 时 must proven（kb-r4a）。
    todo_id 可选；租户键受 KB_EXPERIENCE_TENANT_FILTER 控制。
    """
    mode = (os.getenv("KB_EXPERIENCE_TENANT_FILTER") or "auto").strip().lower()
    phase_val = phase.value if hasattr(phase, "value") else str(phase)
    fl: dict[str, Any] = {"phase": phase_val}
    if not kb_experience_effectiveness_soft_enabled():
        fl["effectiveness"] = "proven"
    if todo_id:
        fl["todo_id"] = todo_id

    if mode in ("off", "false", "0", "no"):
        return fl

    use_defaults = _env_bool("KB_EXPERIENCE_TENANT_DEFAULTS", default=True)
    ws, pr = pick_workspace_scope_from_context(target_context, include_env_defaults=use_defaults)
    # auto / strict / on：在解析到租户键时加入 Qdrant must 条件（strict 未来可扩展为必填）
    if mode in ("auto", "strict", "on", "true", "1", "yes"):
        if ws:
            fl["workspace_id"] = ws
        if pr:
            fl["project_id"] = pr
    return fl


def build_experience_retrieve_filters_soft(
    *,
    target_context: dict[str, Any] | None,
    todo_id: str,
) -> dict[str, Any]:
    """
    kb-r3b：经验检索 **不含** phase / effectiveness must，依赖向量召回 + `apply_soft_retrieval_scoring`。
    租户与 todo 逻辑与 `build_experience_retrieve_filters` 一致。
    """
    mode = (os.getenv("KB_EXPERIENCE_TENANT_FILTER") or "auto").strip().lower()
    fl: dict[str, Any] = {}
    if todo_id:
        fl["todo_id"] = todo_id

    if mode in ("off", "false", "0", "no"):
        return fl

    use_defaults = _env_bool("KB_EXPERIENCE_TENANT_DEFAULTS", default=True)
    ws, pr = pick_workspace_scope_from_context(target_context, include_env_defaults=use_defaults)
    if mode in ("auto", "strict", "on", "true", "1", "yes"):
        if ws:
            fl["workspace_id"] = ws
        if pr:
            fl["project_id"] = pr
    return fl


def build_experience_legacy_collection_retrieve_filters(*, phase: Any, todo_id: str) -> dict[str, Any]:
    """
    kb-r1d：对独立 **legacy** experience collection 检索时 **不加** workspace/project must，
    以便召回无租户字段的旧点；保留 phase；proven must 受 KB_EXPERIENCE_EFFECTIVENESS_SOFT 控制（kb-r4a）。
    """
    phase_val = phase.value if hasattr(phase, "value") else str(phase)
    fl: dict[str, Any] = {"phase": phase_val}
    if not kb_experience_effectiveness_soft_enabled():
        fl["effectiveness"] = "proven"
    if todo_id:
        fl["todo_id"] = todo_id
    return fl


def build_experience_legacy_collection_retrieve_filters_soft(*, todo_id: str) -> dict[str, Any]:
    """kb-r3b：legacy 经验集合软检索 — 仅可选 todo_id，无 phase / proven must。"""
    fl: dict[str, Any] = {}
    if todo_id:
        fl["todo_id"] = todo_id
    return fl


def is_legacy_experience_payload(payload: dict[str, Any] | None) -> bool:
    """无 schema_version 或非 kb-exp-v1 视为旧版写入（向量侧可能曾 embed 大块 JSON）。"""
    if not payload:
        return True
    return (payload.get("schema_version") or "") != "kb-exp-v1"


def experience_payload_snippet_for_display(payload: dict[str, Any] | None) -> str:
    """
    检索命中注入决策上下文前的可读摘要。
    旧 payload：禁止把整块 context_snapshot JSON 当主摘要；长 summary 硬截断。
    """
    if not payload:
        return ""
    if is_legacy_experience_payload(payload):
        phase = str(payload.get("phase") or "").strip()
        skill = str(payload.get("skill_id") or "").strip()
        target = str(payload.get("target") or "").strip()[:200]
        parts: list[str] = ["[legacy-experience]"]
        if phase:
            parts.append(f"phase={phase}")
        if skill:
            parts.append(f"skill={skill}")
        if target:
            parts.append(f"target={target}")
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            s = summary.strip()
            if len(s) > 800:
                parts.append(s[:400].rstrip() + "…[legacy summary truncated]")
            else:
                parts.append(s[:600])
        return " | ".join(parts)[:1200]
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    ctx = payload.get("context_snapshot")
    if isinstance(ctx, dict):
        kb = ctx.get("kb_features")
        if isinstance(kb, dict):
            ip = kb.get("intent_projection")
            if isinstance(ip, str) and ip.strip():
                return ip.strip()[:800]
        try:
            return json.dumps(ctx, ensure_ascii=False)[:1200]
        except Exception:
            return str(ctx)[:1200]
    if isinstance(ctx, list):
        try:
            return json.dumps(ctx, ensure_ascii=False)[:1200]
        except Exception:
            return str(ctx)[:1200]
    return ""


class ExperiencePayload(BaseModel):
    """Qdrant experience collection 的 payload 契约。"""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["experience"] = "experience"
    schema_version: str = Field(default="kb-exp-v1", min_length=1, max_length=64)
    skill_id: str = Field(..., min_length=1)
    phase: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    todo_id: str | None = None
    agent_role: str | None = None
    target: str | None = None
    effectiveness: str = Field(default="pending", min_length=1, max_length=32)
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="runtime_experience", min_length=1, max_length=128)
    created_at: str = Field(..., min_length=1)
    updated_at: str = Field(..., min_length=1)
    workspace_id: str | None = Field(default=None, max_length=256)
    project_id: str | None = Field(default=None, max_length=256)
    artifact_ref: str = Field(..., min_length=1)
    event_id: str | None = None
    memory_path: str | None = None
    chunk_id: str | None = Field(default=None, max_length=256)
    summary: str = Field(default="", max_length=32000)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("effectiveness")
    @classmethod
    def _effectiveness_token(cls, v: str) -> str:
        s = (v or "").strip().lower()
        allowed = frozenset({"pending", "proven", "deprecated"})
        if s not in allowed:
            raise ValueError(f"effectiveness must be one of {sorted(allowed)}")
        return s


def experience_payload_dict_for_upsert(candidate: Any, *, now_iso: str) -> dict[str, Any]:
    """由写入候选（ExperienceCandidate）构造经校验的 payload dict（供 Qdrant upsert）。"""
    model = ExperiencePayload(
        skill_id=getattr(candidate, "skill_id", "") or "",
        phase=getattr(candidate, "phase", "") or "",
        task_id=getattr(candidate, "task_id", "") or "",
        todo_id=_strip_or_none(getattr(candidate, "todo_id", None)),
        agent_role=_strip_or_none(getattr(candidate, "agent_role", None)),
        target=_strip_or_none(getattr(candidate, "target", None)),
        effectiveness=getattr(candidate, "effectiveness", "pending") or "pending",
        source=getattr(candidate, "source", "runtime_experience") or "runtime_experience",
        created_at=now_iso,
        updated_at=now_iso,
        workspace_id=_strip_or_none(getattr(candidate, "workspace_id", None)),
        project_id=_strip_or_none(getattr(candidate, "project_id", None)),
        artifact_ref=getattr(candidate, "artifact_ref", "") or "",
        event_id=_strip_or_none(getattr(candidate, "event_id", None)),
        memory_path=_strip_or_none(getattr(candidate, "memory_path", None)),
        chunk_id=_strip_or_none(getattr(candidate, "chunk_id", None)),
        summary=getattr(candidate, "summary_text", "") or "",
        context_snapshot=dict(getattr(candidate, "context_snapshot", None) or {}),
    )
    return model.model_dump(mode="json", exclude_none=True)
