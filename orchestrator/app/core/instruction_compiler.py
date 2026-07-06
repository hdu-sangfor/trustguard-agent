"""
InstructionCompiler：PlanItem + context_chunk_refs → CompiledInstruction。

- R4a：target / 基础字段直通。
- R4b：按 skill_id 调用 `skill_rules.build_params_for_skill`（首个 skill：nmap，metadata 白名单）。
- R4c：产出走 `plan_validation.validate_compiled_instruction`（R1b）；target 长度裁剪；
  `timeout_seconds` 按 skill 上限与 PlanConstraints 取 min；nmap 同步裁剪 `params.timeout`。
- r4d / kb-r5b：`context_chunk_refs` 逐条 `read_chunk`（存在性 + 租户 ACL）；失败 Fast-Fail；
  成功则将正文写入 `CompiledInstruction.resolved_context_chunks`（与 ref 同序）。
  总序列化大小受 `ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES`（默认 2MiB）约束。
  `ORCH_COMPILER_VERIFY_CHUNKS=false` 时跳过校验且 **不** 填充 resolved（仅排障）。
  同一 PlanItem 内对重复 `chunk_id` 会去重；结构非法的 `chunk_id` 触发 **编译失败**（不再静默跳过）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any
from collections import OrderedDict

from pydantic import BaseModel, Field, model_validator

from app.core.chunk_store import ChunkStoreError, read_chunk
from app.core.skill_rules import build_params_for_skill, merge_resolved_context_into_skill_params
from app.plan_models import (
    CompiledInstruction,
    PlanErrorCode,
    PlanErrorEnvelope,
    PlanItem,
    PlanSchemaVersion,
    ResolvedContextChunk,
)

# 防止异常长 URL/主机串进入执行层；与 target_scope 解耦的硬上限
MAX_COMPILE_TARGET_CHARS = 4096

# CompiledInstruction.timeout_seconds 按 skill 再裁剪（executor 行为对齐）
_COMPILED_TIMEOUT_CAP_BY_SKILL: dict[str, int] = {
    "nmap": 120,
}


def _default_compiled_timeout_cap() -> int:
    """每个 Plan 项的默认超时上限（秒）。读 ORCH_SKILL_TIMEOUT_CAP_SECONDS，默认 300（5 分钟）。"""
    raw = (os.getenv("ORCH_SKILL_TIMEOUT_CAP_SECONDS") or "").strip()
    if not raw:
        return 300
    try:
        return max(30, min(int(raw), 3600))
    except ValueError:
        return 300
_COMPILE_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

logger = logging.getLogger(__name__)


def _compile_summary_log_enabled() -> bool:
    v = (os.getenv("ORCH_COMPILER_COMPILE_SUMMARY_LOG") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _emit_compile_summary_json(plan_item: PlanItem, final: CompiledInstruction) -> None:
    """
    编译成功后的单行结构化摘要（ELK/Loki 友好）；不含密钥与 plan_intent 全文。
    用于对账 PlanItem 与编译后 skill、意图截断与超时。
    """
    if not _compile_summary_log_enabled():
        return
    intent = str((final.params or {}).get("plan_intent") or "")
    preview = intent[:50].replace("\n", " ") + ("..." if len(intent) > 50 else "")
    ih = hashlib.sha256(intent.encode("utf-8")).hexdigest()[:8] if intent else ""
    md = plan_item.metadata if isinstance(plan_item.metadata, dict) else {}
    upstream = str(md.get("upstream_skill_id") or "").strip()
    trace_data: dict[str, Any] = {
        "trace_type": "COMPILE_SUMMARY",
        "task_id": final.task_id,
        "plan_id": final.plan_id,
        "plan_item_skill_id": (plan_item.skill_id or "").strip(),
        "planner_upstream_skill_hint": upstream,
        "compiled_skill_id": (final.skill_id or "").strip(),
        "intent_len": len(intent),
        "intent_hash_prefix": ih,
        "intent_preview": preview,
        "timeout_seconds": int(final.timeout_seconds),
        "params_timeout": (final.params or {}).get("timeout"),
        "has_resolved_chunks": "resolved_chunks" in (final.params or {}),
    }
    logger.info(json.dumps(trace_data, ensure_ascii=False))


def _capped_instruction_timeout(skill_id: str, constraint_seconds: int) -> int:
    sid = (skill_id or "").strip().lower()
    cap = _COMPILED_TIMEOUT_CAP_BY_SKILL.get(sid, _default_compiled_timeout_cap())
    return max(1, min(int(constraint_seconds), cap))


def _trim_target_string(target: str) -> str:
    t = (target or "").strip()
    if len(t) > MAX_COMPILE_TARGET_CHARS:
        t = t[:MAX_COMPILE_TARGET_CHARS].rstrip()
    return t


def _chunk_verify_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    raw = (os.getenv("ORCH_COMPILER_VERIFY_CHUNKS", "true") or "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _resolved_context_bundle_max_bytes() -> int:
    raw = (os.getenv("ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES") or "").strip()
    if not raw:
        return 2 * 1024 * 1024
    try:
        return max(1, min(int(raw), 32 * 1024 * 1024))
    except ValueError:
        return 2 * 1024 * 1024


def _compile_cache_enabled() -> bool:
    raw = (os.getenv("ORCH_COMPILER_ENABLE_CACHE", "false") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _compile_cache_max_entries() -> int:
    raw = (os.getenv("ORCH_COMPILER_CACHE_MAX_ENTRIES") or "").strip()
    if not raw:
        return 128
    try:
        return max(1, min(int(raw), 4096))
    except ValueError:
        return 128


def _compile_cache_key(plan_item: PlanItem, *, target: str, verify_chunks: bool) -> str:
    payload = {
        "plan_item": plan_item.model_dump(mode="json"),
        "target": target,
        "verify_chunks": bool(verify_chunks),
        # 关键环境因子纳入 key，避免配置变化后命中旧结果。
        "env": {
            "ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES": os.getenv("ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES", ""),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compile_cache_get(key: str) -> CompiledInstruction | None:
    rec = _COMPILE_CACHE.get(key)
    if rec is None:
        return None
    # LRU: 命中后移动到末尾
    _COMPILE_CACHE.move_to_end(key)
    try:
        return CompiledInstruction.model_validate(rec)
    except Exception:
        _COMPILE_CACHE.pop(key, None)
        return None


def _compile_cache_put(key: str, instruction: CompiledInstruction) -> None:
    _COMPILE_CACHE[key] = instruction.model_dump(mode="json")
    _COMPILE_CACHE.move_to_end(key)
    max_n = _compile_cache_max_entries()
    while len(_COMPILE_CACHE) > max_n:
        _COMPILE_CACHE.popitem(last=False)


def _clear_compile_cache_for_tests() -> None:
    _COMPILE_CACHE.clear()


def _dedupe_plan_chunk_refs(refs: list[Any]) -> list[Any]:
    """同一 PlanItem 内对 chunk_id 去重，保留首次出现的 tenant 绑定，降低编译预算与重复检索死循环。"""
    seen: set[str] = set()
    out: list[Any] = []
    for ref in refs or []:
        cid = str(getattr(ref, "chunk_id", "") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(ref)
    return out


def _resolve_context_chunks(plan_item: PlanItem) -> tuple[PlanErrorEnvelope | None, list[ResolvedContextChunk]]:
    """逐 ref read_chunk + ACL；成功返回 (None, resolved 列表)。"""
    task_id = plan_item.task_id
    out: list[ResolvedContextChunk] = []
    budget = _resolved_context_bundle_max_bytes()
    used = 0
    for ref in _dedupe_plan_chunk_refs(list(plan_item.context_chunk_refs or [])):
        try:
            rec = read_chunk(
                task_id,
                ref.chunk_id,
                expect_tenant_id=ref.tenant_id,
                deny_tenant_mismatch=True,
                require_tenant_when_bound=True,
            )
        except ChunkStoreError as e:
            if e.code == "CHUNK_INVALID_CHUNK_ID":
                return (
                    PlanErrorEnvelope(
                        schema_version=PlanSchemaVersion.V1,
                        code=PlanErrorCode.COMPILATION_FAILED,
                        message="invalid chunk_id in context_chunk_refs (structural / hallucinated id)",
                        details={
                            "chunk_id": ref.chunk_id,
                            "plan_id": plan_item.plan_id,
                            "task_id": task_id,
                            "suggested_action": "retry_with_replan",
                            "inner_code": e.code,
                        },
                    ),
                    [],
                )
            forbidden_codes = frozenset({"CHUNK_TENANT_MISMATCH", "CHUNK_TENANT_REQUIRED"})
            pcode = PlanErrorCode.CHUNK_FORBIDDEN if e.code in forbidden_codes else PlanErrorCode.COMPILATION_FAILED
            return (
                PlanErrorEnvelope(
                    schema_version=PlanSchemaVersion.V1,
                    code=pcode,
                    message=e.message,
                    details={
                        "chunk_id": ref.chunk_id,
                        "plan_id": plan_item.plan_id,
                        "task_id": task_id,
                        "suggested_action": "retry_with_replan",
                        "inner_code": e.code,
                    },
                ),
                [],
            )
        if rec is None:
            return (
                PlanErrorEnvelope(
                    schema_version=PlanSchemaVersion.V1,
                    code=PlanErrorCode.CHUNK_NOT_FOUND,
                    message="referenced chunk not found or no longer available",
                    details={
                        "chunk_id": ref.chunk_id,
                        "plan_id": plan_item.plan_id,
                        "task_id": task_id,
                        "suggested_action": "retry_with_replan",
                    },
                ),
                [],
            )
        content = rec.get("content")
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        try:
            piece = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            piece = b"null"
        if len(piece) > budget:
            return (
                PlanErrorEnvelope(
                    schema_version=PlanSchemaVersion.V1,
                    code=PlanErrorCode.COMPILATION_FAILED,
                    message="resolved chunk body exceeds ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES budget",
                    details={
                        "chunk_id": ref.chunk_id,
                        "plan_id": plan_item.plan_id,
                        "task_id": task_id,
                        "suggested_action": "retry_with_replan",
                        "max_bytes": budget,
                        "size_bytes": len(piece),
                    },
                ),
                [],
            )
        used += len(piece)
        if used > budget:
            return (
                PlanErrorEnvelope(
                    schema_version=PlanSchemaVersion.V1,
                    code=PlanErrorCode.COMPILATION_FAILED,
                    message="resolved context chunks exceed ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES total budget",
                    details={
                        "plan_id": plan_item.plan_id,
                        "task_id": task_id,
                        "suggested_action": "retry_with_replan",
                        "max_bytes": budget,
                        "accumulated_bytes": used,
                    },
                ),
                [],
            )
        out.append(
            ResolvedContextChunk(
                chunk_id=ref.chunk_id,
                chunk_type=str(meta.get("chunk_type") or "")[:256],
                body=content,
            )
        )
    return None, out


def _trim_params_for_r4c(skill_id: str, params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params)
    sid = (skill_id or "").strip().lower()
    if sid == "nmap" and "timeout" in out:
        try:
            tv = int(out["timeout"])
            out["timeout"] = max(1, min(tv, 120))
        except (TypeError, ValueError):
            pass
    return out


class InstructionCompileResult(BaseModel):
    """单次编译结果：成功携带 CompiledInstruction，失败携带 PlanErrorEnvelope。"""

    ok: bool = Field(..., description="是否编译成功")
    instruction: CompiledInstruction | None = Field(default=None)
    error: PlanErrorEnvelope | None = Field(default=None)

    @model_validator(mode="after")
    def _consistency(self) -> InstructionCompileResult:
        if self.ok:
            if self.instruction is None:
                raise ValueError("ok=True requires instruction")
            if self.error is not None:
                raise ValueError("ok=True must not set error")
        else:
            if self.error is None:
                raise ValueError("ok=False requires error")
        return self


def _emit_compile_sli(res: InstructionCompileResult, duration_ms: float | None = None) -> InstructionCompileResult:
    from app.orchestrator_sli_metrics import record_instruction_compile_result

    code: str | None = None
    if res.error is not None:
        try:
            code = res.error.code.value
        except Exception:
            code = str(res.error.code)
    record_instruction_compile_result(res.ok, code, duration_ms=duration_ms)
    return res


class InstructionCompiler:
    """
    将计划项编译为执行指令。

    target：`execution_target` 优先，否则 `constraints.target_scope`。
    params：`build_params_for_skill`（R4b 起按 skill 规则填充，默认 {}）。
    verify_chunks：默认读环境 `ORCH_COMPILER_VERIFY_CHUNKS`（true 时对 ref 做 r4d 校验）。
    """

    def compile(
        self,
        plan_item: PlanItem,
        *,
        execution_target: str | None = None,
        verify_chunks: bool | None = None,
    ) -> InstructionCompileResult:
        t0 = time.perf_counter()

        def _emit(res: InstructionCompileResult) -> InstructionCompileResult:
            return _emit_compile_sli(res, duration_ms=(time.perf_counter() - t0) * 1000.0)

        target = (execution_target or "").strip() or (plan_item.constraints.target_scope or "").strip()
        target = _trim_target_string(target)
        if not target:
            return _emit(
                InstructionCompileResult(
                    ok=False,
                    error=PlanErrorEnvelope(
                        schema_version=PlanSchemaVersion.V1,
                        code=PlanErrorCode.COMPILATION_FAILED,
                        message="execution target is empty after compile",
                        details={"plan_id": plan_item.plan_id, "skill_id": plan_item.skill_id},
                    ),
                )
            )

        verify_on = _chunk_verify_enabled(verify_chunks)
        cache_key = ""
        if _compile_cache_enabled():
            cache_key = _compile_cache_key(plan_item, target=target, verify_chunks=verify_on)
            hit = _compile_cache_get(cache_key)
            if hit is not None:
                _emit_compile_summary_json(plan_item, hit)
                return _emit(InstructionCompileResult(ok=True, instruction=hit))

        deduped_refs = _dedupe_plan_chunk_refs(list(plan_item.context_chunk_refs or []))
        chunk_plan_item = plan_item.model_copy(update={"context_chunk_refs": deduped_refs})
        resolved_chunks: list[ResolvedContextChunk] = []
        if verify_on:
            chunk_err, resolved_chunks = _resolve_context_chunks(chunk_plan_item)
            if chunk_err is not None:
                return _emit(InstructionCompileResult(ok=False, error=chunk_err))

        params = _trim_params_for_r4c(
            plan_item.skill_id,
            build_params_for_skill(plan_item),
        )
        params = merge_resolved_context_into_skill_params(
            plan_item.skill_id,
            params,
            resolved_chunks,
        )
        timeout_seconds = _capped_instruction_timeout(
            plan_item.skill_id,
            plan_item.constraints.timeout_seconds,
        )

        instruction = CompiledInstruction(
            schema_version=PlanSchemaVersion.V1,
            task_id=plan_item.task_id,
            plan_id=plan_item.plan_id,
            skill_id=plan_item.skill_id,
            target=target,
            params=params,
            timeout_seconds=timeout_seconds,
            context_chunk_refs=list(deduped_refs),
            resolved_context_chunks=resolved_chunks,
        )
        payload = json.loads(instruction.model_dump_json())
        from app.plan_validation import validate_compiled_instruction as _validate_compiled

        ok, err = _validate_compiled(payload)
        if not ok or err is not None:
            # 单测 monkeypatch 等场景下可能出现「另一份 plan_models 里的 PlanErrorEnvelope」；
            # 经 model_dump → model_validate 归一到本模块绑定的类型，避免 Pydantic 判定非实例。
            err_out: PlanErrorEnvelope | None = None
            if err is not None:
                err_out = PlanErrorEnvelope.model_validate(err.model_dump())
            return _emit(InstructionCompileResult(ok=False, error=err_out))

        final = CompiledInstruction.model_validate(payload)
        _emit_compile_summary_json(plan_item, final)
        if cache_key:
            _compile_cache_put(cache_key, final)
        return _emit(InstructionCompileResult(ok=True, instruction=final))


def compile_plan_item(
    plan_item: PlanItem,
    *,
    execution_target: str | None = None,
    verify_chunks: bool | None = None,
    compiler: InstructionCompiler | None = None,
) -> InstructionCompileResult:
    """模块级入口，便于测试与编排器注入自定义 Compiler 子类。"""
    c = compiler or InstructionCompiler()
    return c.compile(plan_item, execution_target=execution_target, verify_chunks=verify_chunks)
