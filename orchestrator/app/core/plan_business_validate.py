"""
PlanList 业务层校验（R2c）：skill 须在当相可用列表内、target_scope 与任务目标一致、chunk 引用数上限。

在结构化 schema（validate_plan_list）通过之后、写入 target_context / checkpoint 之前调用。
"""

from __future__ import annotations

# 校验失败时写入 target_context，供断点与排障（与 LATEST_PLAN_LIST 分离）
PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY = "_latest_plan_list_validation_error"

import os
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence
from urllib.parse import urlparse

from app.enums import Phase
from app.plan_feature_flags import orch_plan_kit_anchor_skill_enabled
from app.core.capability_kits import (
    effective_plan_item_kit_id,
    get_kit_member_tools,
    get_kit_phase_allowlist,
    pick_kit_anchor_skill,
)
from app.core.phase_capability_policy import effective_plan_item_execution_kind
from app.plan_models import PlanErrorCode, PlanErrorEnvelope, PlanList, PlanSchemaVersion

_SCOPE_LOCALHOST_ALIAS = (
    os.getenv("ORCHESTRATOR_LOCALHOST_ALIAS") or os.getenv("EXECUTOR_LOCALHOST_ALIAS") or "host.docker.internal"
).strip()


def plan_max_chunk_refs_per_item() -> int:
    raw = (os.getenv("ORCH_PLAN_MAX_CHUNK_REFS_PER_ITEM") or "").strip()
    if not raw:
        return 32
    try:
        v = int(raw)
        return max(0, min(v, 500))
    except ValueError:
        return 32


def plan_target_scope_max_len() -> int:
    raw = (os.getenv("ORCH_PLAN_TARGET_SCOPE_MAX_LEN") or "").strip()
    if not raw:
        return 4096
    try:
        return max(64, min(int(raw), 16384))
    except ValueError:
        return 4096


def plan_max_chunk_ref_repeat_per_task() -> int:
    """同一 chunk_id 被多轮 Plan 反复引用的上限（防 LLM 死磕同一上下文块）。"""
    raw = (os.getenv("ORCH_PLAN_MAX_CHUNK_REF_REPEAT_PER_TASK") or "16").strip()
    try:
        return max(1, min(int(raw), 500))
    except ValueError:
        return 16


def _host_of(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if "://" in s:
        try:
            return urlparse(s).hostname or s.split("/")[0].split(":")[0] or ""
        except Exception:
            return s.split("/")[0].split(":")[0] or ""
    return s.split(":")[0].split("/")[0] or ""


def _normalize_scope_host(host: str) -> str:
    h = (host or "").strip().lower()
    if not h:
        return ""
    if h in ("127.0.0.1", "localhost", "::1"):
        return (_SCOPE_LOCALHOST_ALIAS or "host.docker.internal").lower()
    return h


def _target_scope_coherent_with_task(target_scope: str, task_target: str) -> bool:
    scope = (target_scope or "").strip()
    if len(scope) > plan_target_scope_max_len():
        return False
    tt = (task_target or "").strip()
    if not tt:
        return True
    allowed_h = _normalize_scope_host(_host_of(tt))
    if not allowed_h:
        return True
    sl = scope.lower()
    if allowed_h in sl:
        return True
    scope_h = _normalize_scope_host(_host_of(scope))
    return bool(scope_h) and scope_h == allowed_h


def _resolve_skill(raw: str, available: Sequence[str], aliases: Mapping[str, str] | None) -> str:
    sid = (raw or "").strip()
    if not sid:
        return ""
    avail = list(available)
    if sid in avail:
        return sid
    mapped = (aliases or {}).get(sid.lower(), "")
    if mapped and mapped in avail:
        return mapped
    return sid


def _reject(message: str, *, violations: List[Dict[str, Any]]) -> tuple[bool, PlanErrorEnvelope]:
    details: Dict[str, Any] = {
        "violations": violations,
        "suggested_action": "retry_with_replan",
    }
    return False, PlanErrorEnvelope(
        schema_version=PlanSchemaVersion.V1,
        code=PlanErrorCode.INVALID_PLAN_LIST,
        message=message,
        details=details,
    )


def validate_plan_list_business(
    plan_list: PlanList,
    *,
    expected_task_id: str,
    available_skill_ids: Sequence[str],
    task_target: str,
    skill_aliases: Mapping[str, str] | None = None,
    max_chunk_refs_per_item: int | None = None,
    current_phase: Phase | None = None,
    known_chunk_ids: Sequence[str] | None = None,
    chunk_ref_counts: MutableMapping[str, int] | None = None,
    max_chunk_ref_repeat: int | None = None,
) -> tuple[bool, PlanErrorEnvelope | None]:
    """
    返回 (True, None) 或 (False, envelope)；envelope.code 恒为 INVALID_PLAN_LIST，
    details.violations 为逐条机读原因。

    current_phase：非空时执行阶段相关业务校验。
    """
    cap = max_chunk_refs_per_item if max_chunk_refs_per_item is not None else plan_max_chunk_refs_per_item()
    violations: List[Dict[str, Any]] = []
    exp = (expected_task_id or "").strip()
    got = (plan_list.task_id or "").strip()
    if exp and got and exp != got:
        # LLM occasionally makes 1-char typos in long hex task-ids (substitution/transposition).
        # Accept if edit distance <= 2 to avoid spurious PLAN_LIST_BUSINESS_REJECT loops.
        # Auto-correct the task_id in the plan_list so downstream validation uses the real id.
        def _edit_distance(a: str, b: str) -> int:
            if abs(len(a) - len(b)) > 3:
                return 999
            m, n = len(a), len(b)
            dp = list(range(n + 1))
            for i in range(1, m + 1):
                prev = dp[0]
                dp[0] = i
                for j in range(1, n + 1):
                    tmp = dp[j]
                    dp[j] = prev if a[i - 1] == b[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
                    prev = tmp
            return dp[n]
        dist = _edit_distance(exp, got)
        if dist <= 2:
            # Tolerate minor LLM hallucination — silently correct task_id
            try:
                plan_list.task_id = exp
            except Exception:
                pass
        else:
            violations.append(
                {
                    "code": "task_id_mismatch",
                    "message": "plan_list.task_id does not match orchestrator task",
                    "expected_task_id": exp,
                    "got_task_id": got,
                }
            )

    avail_set = frozenset(available_skill_ids or [])
    # None → 不校验（调用方未提供可信 chunk 集合）；空列表 → 校验但无合法 chunk，任何引用均拒绝
    known_chunks: frozenset[str] | None = (
        frozenset(str(c).strip() for c in known_chunk_ids if str(c).strip())
        if known_chunk_ids is not None
        else None
    )
    repeat_cap = max_chunk_ref_repeat if max_chunk_ref_repeat is not None else plan_max_chunk_ref_repeat_per_task()
    # 默认开启：与 InstructionCompiler read_chunk 对齐，减少幻觉 chunk 进入派发/编译死循环。
    # 本地排障可设 ORCH_PLAN_VALIDATE_CHUNK_IN_STORE=false；单测通过 monkeypatch 关闭。
    strict_chunk_store = (os.getenv("ORCH_PLAN_VALIDATE_CHUNK_IN_STORE", "true") or "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    for item in plan_list.items or []:
        pid = (item.plan_id or "").strip() or "<missing_plan_id>"
        planner_skill = (item.skill_id or "").strip()
        skip_missing_skill_id = False
        if not planner_skill:
            if orch_plan_kit_anchor_skill_enabled():
                kid_anchor = effective_plan_item_kit_id(item, plan_list)
                if kid_anchor:
                    anchor = pick_kit_anchor_skill(kid_anchor, available_skill_ids)
                    if anchor:
                        planner_skill = anchor
                    else:
                        violations.append(
                            {
                                "code": "kit_anchor_no_available_member",
                                "plan_id": pid,
                                "kit_id": kid_anchor,
                                "message": "kit has no member in available_skill_ids for anchor resolution",
                            }
                        )
                        skip_missing_skill_id = True
            if not planner_skill and not skip_missing_skill_id:
                violations.append(
                    {
                        "code": "missing_planner_skill_id",
                        "plan_id": pid,
                        "message": (
                            "PlanItem.skill_id is empty; set a skill from available_skill_ids, "
                            "or enable ORCH_PLAN_KIT_ANCHOR_SKILL and declare kit_id / PlanList.kit_id"
                        ),
                    }
                )

        resolved = _resolve_skill(planner_skill, available_skill_ids, skill_aliases) if planner_skill else ""
        if planner_skill and (not resolved or resolved not in avail_set):
            violations.append(
                {
                    "code": "unknown_skill",
                    "plan_id": pid,
                    "skill_id": item.skill_id,
                    "resolved_skill_id": resolved,
                }
            )
        if current_phase is not None and planner_skill:
            try:
                effective_plan_item_execution_kind(
                    skill_id=resolved or planner_skill,
                    metadata=item.metadata,
                    current_phase=current_phase,
                )
            except ValueError as e:
                violations.append(
                    {
                        "code": "execution_kind_conflict",
                        "plan_id": pid,
                        "skill_id": item.skill_id,
                        "resolved_skill_id": resolved,
                        "message": str(e),
                    }
                )
        kid = effective_plan_item_kit_id(item, plan_list)
        if kid:
            kit_members = get_kit_member_tools(kid)
            if kit_members is None:
                violations.append(
                    {
                        "code": "unknown_capability_kit",
                        "plan_id": pid,
                        "kit_id": kid,
                    }
                )
            else:
                rlow = (resolved or "").strip().lower()
                if rlow and resolved in avail_set and resolved not in frozenset(kit_members):
                    violations.append(
                        {
                            "code": "skill_not_in_capability_kit",
                            "plan_id": pid,
                            "kit_id": kid,
                            "skill_id": item.skill_id,
                            "resolved_skill_id": resolved,
                        }
                    )
                phase_allow = get_kit_phase_allowlist(kid)
                if current_phase is not None and phase_allow is not None:
                    if current_phase.value not in phase_allow:
                        violations.append(
                            {
                                "code": "capability_kit_phase_not_allowed",
                                "plan_id": pid,
                                "kit_id": kid,
                                "current_phase": current_phase.value,
                                "allowed_phases": sorted(phase_allow),
                            }
                        )
        if not _target_scope_coherent_with_task(item.constraints.target_scope, task_target):
            violations.append(
                {
                    "code": "target_scope_mismatch",
                    "plan_id": pid,
                    "target_scope": item.constraints.target_scope,
                    "task_target": task_target,
                }
            )
        n_refs = len(item.context_chunk_refs or [])
        if cap >= 0 and n_refs > cap:
            violations.append(
                {
                    "code": "chunk_refs_exceeded",
                    "plan_id": pid,
                    "ref_count": n_refs,
                    "max_allowed": cap,
                }
            )
        for ref in item.context_chunk_refs or []:
            cid = (ref.chunk_id or "").strip()
            _CHUNK_PREFIX = "chk-"
            if not cid.startswith(_CHUNK_PREFIX) or len(cid[len(_CHUNK_PREFIX):]) < 16:
                violations.append(
                    {
                        "code": "invalid_chunk_ref_format",
                        "plan_id": pid,
                        "chunk_id": cid,
                        "message": (
                            "chunk_id must start with 'chk-' followed by at least 16 alphanumeric characters; "
                            "do NOT use KB tier structure keys like '_tier0' or '_tier1' as chunk IDs; "
                            "only use real chunk IDs explicitly present in retrieval context"
                        ),
                    }
                )
                continue
            if known_chunks is not None and cid not in known_chunks:
                violations.append(
                    {
                        "code": "unknown_chunk_ref",
                        "plan_id": pid,
                        "chunk_id": cid,
                        "message": (
                            "chunk_id is not in current retrieval context; only reference real chunk IDs "
                            "present in kb_hits/tier context for this tick"
                        ),
                    }
                )
                continue
            if chunk_ref_counts is not None and repeat_cap > 0:
                prev = int(chunk_ref_counts.get(cid) or 0)
                if prev >= repeat_cap:
                    violations.append(
                        {
                            "code": "chunk_ref_repeat_budget_exceeded",
                            "plan_id": pid,
                            "chunk_id": cid,
                            "prior_uses": prev,
                            "max_allowed": repeat_cap,
                            "message": (
                                "this chunk_id has been referenced too many times across planner cycles; "
                                "summarize findings in plan_content or use different evidence"
                            ),
                        }
                    )
                    continue
            if strict_chunk_store and exp:
                try:
                    from app.core.chunk_store import ChunkStoreError, read_chunk

                    tid_expect = (ref.tenant_id or "").strip() or None
                    rec = read_chunk(
                        exp,
                        cid,
                        expect_tenant_id=tid_expect,
                        deny_tenant_mismatch=True,
                        require_tenant_when_bound=True,
                    )
                except ChunkStoreError as e:
                    violations.append(
                        {
                            "code": "chunk_store_access_denied",
                            "plan_id": pid,
                            "chunk_id": cid,
                            "inner_code": getattr(e, "code", ""),
                            "message": str(e.message or e),
                        }
                    )
                    continue
                if rec is None:
                    violations.append(
                        {
                            "code": "chunk_not_found_in_workspace",
                            "plan_id": pid,
                            "chunk_id": cid,
                            "message": (
                                "chunk_id is not present in task workspace store (expired, wrong task, "
                                "or hallucinated); only reference chunks that exist on disk for this task"
                            ),
                        }
                    )

    if violations:
        return _reject("PlanList failed business validation", violations=violations)
    return True, None


def stash_plan_list_validation_error(target_context: MutableMapping[str, Any], envelope: PlanErrorEnvelope) -> None:
    """写入 target_context 供断点/排障（不写入 _latest_plan_list）。"""
    from app.structured_error_envelope import plan_error_envelope_to_client_dict

    target_context[PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY] = plan_error_envelope_to_client_dict(envelope)
