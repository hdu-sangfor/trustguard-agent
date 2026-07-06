"""
Orchestrator 状态机：单次 tick 推进逻辑。

由 main 调用，负责「决策 → 执行 → 留痕」单步闭环；
每步执行成功后：命名空间合并 target_context、更新 history_summary、
同步Evidence context、写入 artifacts 摘要，便于红队证据链与续跑。

MQ 模式：轮询已前移至 ExecutionDispatcher，dispatch 返回 SUCCESS/FAILED/TIMEOUT 等终态，本模块不再处理 DISPATCHED。
"""
import asyncio
import json
import logging
import os
import time
import re
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import shlex

from app.enums import coerce_phase_from_llm
from app.models import (
    ActionItem,
    Phase,
    TaskState,
    TaskStatus,
    TraceEvent,
    phase_wall_clock_limit_sec_from_env,
    max_plan_rounds_per_phase_from_env,
)
from app.clients.llm_client import LLMCallFailed, call_plan_list_decision_engine
from app.clients.executor_client import fetch_skills_for_phase
from app.clients.trace_client import emit_trace
from app.clients.evidence_client import put_context, put_artifacts_summary
from app.core.loop_guard import canonical_params, artifact_hash, update_loop_guard
from app.core.workspace_store import write_artifact, write_task_context
from app.core.artifact_reader import load_parsed_from_artifact_ref
from app.core.agent_tools import SkillCallContext, SkillExecutor
from app.core.memory_store import (
    apply_fact_updates,
    build_action_signature,
    detect_repeated_signature,
    sanitize_plan_list_metadata,
)
from app.core.decision_policy import bias_available_skills_for_decision, ensure_baidu_search_offered
from app.core.skill_preflight import ensure_iml_in_context, filter_available_skills_by_iml, preflight_actions

from app.core.coverage_check import compute_report_coverage_gaps
from app.core.recon_dirsearch_seeds import katana_seeds_from_dirsearch_json
from app.core.manager_agent import (
    TodoStatus,
    clear_current_todo_from_context,
    ensure_todos_from_context,
    get_todos_from_state,
    inject_current_todo_into_context,
    load_todos_from_store,
    persist_todos_to_state,
    persist_todos_to_store,
    pick_next_pending_todo,
    set_todo_status,
)
from app.core.task_store import TaskStore, get_task_store_from_env
from app.core.asset_path_profile import (
    compute_asset_path_profile,
    maybe_override_framework_from_asset_profile,
)
from app.core.http_enum_seeds import normalize_url_for_pipeline
from app.core.phase_transition_guard import (
    coverage_skill_ids,
    guard_finish_phase,
    guard_next_phase,
    normalize_framework_unknown_if_needed,
)
from app.core.plan_business_validate import (
    PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY,
    stash_plan_list_validation_error,
    validate_plan_list_business,
)
from app.core.plan_kit_anchor import materialize_kit_anchor_plan_list
from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
from app.core.rag_plan_chunk_refs import merge_kb_hits_into_plan_chunk_refs
from app.core.correlation_ids import attach_correlation
from app.core.plan_execution_dispatch import (
    PLAN_DISPATCH_NEXT_INDEX_KEY,
    try_dispatch_pending_plan_list_item,
)
from app.plan_models import PlanList
from app.structured_error_envelope import plan_error_envelope_to_client_dict

logger = logging.getLogger(__name__)


def _brief_plan_list_violations_for_history(envelope: Any, *, max_items: int = 5) -> str:
    """
    将 PlanList business validation 的 structured_error.details.violations 摘要成单行，便于 LLM 下一轮纠错。
    约束：必须短小，避免 history_summary 爆炸。
    """
    try:
        details = getattr(envelope, "details", None) or {}
        if not isinstance(details, dict):
            return ""
        violations = details.get("violations") or []
        if not isinstance(violations, list) or not violations:
            return ""
        out: list[str] = []
        for v in violations[: max(1, int(max_items or 5))]:
            if not isinstance(v, dict):
                continue
            code = str(v.get("code") or "").strip() or "violation"
            pid = str(v.get("plan_id") or "").strip()
            cid = str(v.get("chunk_id") or "").strip()
            sid = str(v.get("skill_id") or v.get("resolved_skill_id") or "").strip()
            if cid:
                out.append(f"{code}(chunk_id={cid})")
            elif sid and pid:
                out.append(f"{code}(plan_id={pid},skill_id={sid})")
            elif pid:
                out.append(f"{code}(plan_id={pid})")
            else:
                out.append(code)
        return ", ".join(out)
    except Exception:
        return ""


_SCOPE_LOCALHOST_ALIAS = (
    os.getenv("ORCHESTRATOR_LOCALHOST_ALIAS")
    or os.getenv("EXECUTOR_LOCALHOST_ALIAS")
    or "host.docker.internal"
).strip()
_SKILL_ID_ALIASES: dict[str, str] = {
    "web-fingerprint": "whatweb-fingerprint",
    "baidu_search": "baidu-search",
    "baidu search": "baidu-search",
}


def _known_chunk_ids_from_context(target_context: dict[str, Any], task_id: str | None = None) -> list[str]:
    """
    从当前 tick 的检索上下文提取可被 Plan 引用的真实 chunk_id 集合。
    同时枚举 workspace 落盘 chunk 目录，将任务运行期写入的 kb_runtime_experience 等
    task-local chunk 也纳入合法集合，防止 LLM 引用真实但未在 kb_hits 中出现的 chunk_id
    时被误判为幻觉而无限 replan。
    仅用于业务校验层前置拦截 LLM 伪造 chunk_id。
    """
    out: list[str] = []
    seen: set[str] = set()
    for hit in (target_context.get("kb_hits") or []):
        if not isinstance(hit, dict):
            continue
        cid = str(hit.get("chunk_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    # 补充 workspace 落盘的 task-local chunks（e.g. kb_runtime_experience）
    if task_id:
        try:
            from app.core.chunk_store import list_chunk_ids_for_task
            for cid in list_chunk_ids_for_task(task_id):
                if cid and cid not in seen:
                    seen.add(cid)
                    out.append(cid)
        except Exception:
            pass
    return out


def _recon_deterministic_enabled() -> bool:
    """
    RECON 首轮确定性微流开关。
    - true（默认）：先跑固定探测，再放行 LLM 决策
    - false：首轮直接进入 LLM 决策
    """
    return (os.getenv("ORCH_RECON_DETERMINISTIC_ENABLED", "true").strip().lower() == "true")


def _skill_timeout_cap() -> int:
    """每次工具调用允许的最大执行时间（秒）。读 ORCH_SKILL_TIMEOUT_CAP_SECONDS，默认 300（5 分钟）。"""
    raw = (os.getenv("ORCH_SKILL_TIMEOUT_CAP_SECONDS") or "").strip()
    if not raw:
        return 300
    try:
        return max(30, min(int(raw), 3600))
    except ValueError:
        return 300


def _safe_task_id(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", (s or "task").strip())[:96] or "task"


def _shared_target_discovery_dir(task_id: str, run_id: str) -> str:
    """
    任务隔离 discovery 根目录：只落在当前 task/run 下，禁止 host 级共享。
    """
    return f"{_safe_task_id(task_id)}/web-vuln/{run_id}/discovery"


def _shared_target_discovery_abs(task_id: str, run_id: str) -> Path:
    """编排器进程本机读 clustered_targets / katana_urls 时使用。"""
    ws = (os.getenv("WORKSPACE_ROOT", "/data/workspace") or "/data/workspace").strip()
    rel = _shared_target_discovery_dir(task_id, run_id).replace("\\", "/").strip("/")
    return Path(ws) / rel


def _normalize_rel_path(raw: Any) -> str:
    s = str(raw or "").strip().replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    return s.strip("/")


def _looks_like_phase_event_artifact_rel(rel_path: str) -> bool:
    rel = _normalize_rel_path(rel_path)
    return bool(re.search(r"/(RECON|THREAT_MODEL|VULN_SCAN|EXPLOIT|REPORT)/evt-[^/]+/", rel))


def _resolve_read_target_list_rel_path(
    *,
    task_id: str,
    params: dict[str, Any],
    target_context: dict[str, Any] | None,
    shared_discovery_dir: str,
) -> str:
    """
    统一 read_target_list 的 rel_path 选择策略：
    1) 优先使用 katana 产物中的 task_katana_urls_rel；
    2) 次选 task_clustered_targets_rel；
    3) 若已有 rel_path 不是 phase/evt 工件路径且属于当前 task，则保留；
    4) 最后回退到 shared discovery 路径。
    """
    ctx = target_context if isinstance(target_context, dict) else {}
    safe_task = _safe_task_id(task_id)
    current = _normalize_rel_path((params or {}).get("rel_path"))

    def _valid_task_rel(v: Any) -> str:
        rel = _normalize_rel_path(v)
        if not rel:
            return ""
        if not (rel == safe_task or rel.startswith(f"{safe_task}/")):
            return ""
        return rel

    def _from_ctx_key(k: str) -> str:
        rel = _valid_task_rel(ctx.get(k))
        if rel:
            return rel
        di = ctx.get("diagnostics")
        if isinstance(di, dict):
            rel = _valid_task_rel(di.get(k))
            if rel:
                return rel
        kdiag = ctx.get("katana_diagnostics")
        if isinstance(kdiag, dict):
            rel = _valid_task_rel(kdiag.get(k))
            if rel:
                return rel
        return ""

    # 明确优先使用 katana 明确产物路径，避免 LLM 自行拼接 wsref 派生路径出错。
    from_ctx = _from_ctx_key("task_katana_urls_rel")
    if from_ctx:
        return from_ctx
    from_ctx = _from_ctx_key("task_clustered_targets_rel")
    if from_ctx:
        return from_ctx

    if current and _valid_task_rel(current) and not _looks_like_phase_event_artifact_rel(current):
        return current
    return f"{_normalize_rel_path(shared_discovery_dir)}/katana_urls.txt"


def _inject_clustered_targets_preview(state: TaskState) -> None:
    """
    将 clustered_targets.txt / katana_urls.txt 注入 target_context：
    - 预览若干条供 LLM 使用；
    - 全量（有上限）用于资产路径轮廓与 crawler_confirmed_url_set（Fallback 豁免）。
    """
    if not isinstance(state.target_context, dict):
        state.target_context = {}
    run_id = str((state.target_context or {}).get("run_id") or "").strip()
    if not run_id:
        return
    d = _shared_target_discovery_abs(state.task_id, run_id)
    cands = [d / "clustered_targets.txt", d / "katana_urls.txt"]
    all_lines: list[str] = []
    src = ""
    for fp in cands:
        if fp.exists() and fp.is_file():
            try:
                lines = [ln.strip() for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            except Exception:
                lines = []
            if lines:
                src = str(fp)
                all_lines = lines
                break
    cap = max(50, int(os.getenv("ORCHESTRATOR_CLUSTERED_PROFILE_MAX_URLS", "800") or "800"))
    profile_lines = all_lines[:cap]
    state.target_context["clustered_targets_preview"] = profile_lines[:12]
    state.target_context["clustered_targets_total"] = len(all_lines)
    state.target_context["clustered_targets_count"] = len(all_lines)
    if src:
        state.target_context["clustered_targets_source"] = src

    if profile_lines:
        profile = compute_asset_path_profile(profile_lines)
        state.target_context["asset_path_profile"] = profile
        confirmed: set[str] = set()
        for ln in profile_lines:
            nu = normalize_url_for_pipeline(ln)
            if nu:
                confirmed.add(nu)
            confirmed.add(ln.strip())
        state.target_context["crawler_confirmed_url_set"] = sorted(confirmed)[:2000]
        state.target_context["crawler_extraction_note"] = (
            "These URLs come from the crawl pipeline (clustered_targets.txt or katana_urls.txt on task discovery). "
            "They are physically extracted, not random guesses. If host-wide fallback routing is active, still treat "
            "these paths as valid attack surface unless a tool proves otherwise."
        )
        maybe_override_framework_from_asset_profile(state.target_context)


def _auto_mark_exploit_ready_from_nuclei(state: TaskState, artifacts: Dict[str, Any]) -> None:
    """
    When nuclei returns critical or exploit-tagged findings, auto-set exploit_ready=True
    in target_context. This ensures LOOP_BREAK in EXPLOIT phase triggers force-report
    instead of giving the LLM another futile chance.
    """
    if not isinstance(artifacts, dict):
        return
    if not isinstance(state.target_context, dict):
        state.target_context = {}
    if state.target_context.get("exploit_ready"):
        return

    # Collect all findings from multiple possible locations
    kf: List[Any] = artifacts.get("key_findings") or []
    direct_vulns: List[Any] = artifacts.get("vulnerabilities") or []
    llm_ready = artifacts.get("llm_ready") or {}
    lr_vulns: List[Any] = (llm_ready.get("vulnerabilities") if isinstance(llm_ready, dict) else None) or []
    all_findings = (kf if isinstance(kf, list) else []) + \
                   (direct_vulns if isinstance(direct_vulns, list) else []) + \
                   (lr_vulns if isinstance(lr_vulns, list) else [])

    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        sev = str(finding.get("severity") or "").strip().lower()
        tid = str(finding.get("template_id") or finding.get("matcher") or "").strip().lower()
        url = str(finding.get("url") or finding.get("matched_at") or "").strip()

        is_critical = sev == "critical"
        is_exploit_template = any(x in tid for x in ("-cmd", "cmd-rce", "-rce", "exploit", "-exec"))

        if is_critical or is_exploit_template:
            state.target_context["exploit_ready"] = True
            state.target_context["nuclei_exploit_evidence"] = {
                "template_id": tid,
                "severity": sev,
                "url": url,
            }
            if not hasattr(state, "confirmed_facts") or state.confirmed_facts is None:
                state.confirmed_facts = []
            fact = f"nuclei_confirmed: {tid} severity={sev}"
            if fact not in state.confirmed_facts:
                state.confirmed_facts.append(fact)
            return


def _has_confirmed_vuln_for_exploit(state: TaskState) -> tuple[bool, str]:
    """
    EXPLOIT 解锁条件：必须存在漏洞确权事实，避免 RECON/THREAT_MODEL 直跳实弹。
    """
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    for key in ("confirmed_cve", "vuln_confirmed", "exploit_ready"):
        v = ctx.get(key)
        if isinstance(v, bool) and v:
            return True, f"context:{key}=true"
        if isinstance(v, str) and v.strip():
            return True, f"context:{key}"
    facts = [str(x).strip().lower() for x in (getattr(state, "confirmed_facts", None) or []) if str(x).strip()]
    for f in facts:
        if "confirmed_cve" in f or "cve-" in f or "s2-" in f or "vuln_confirmed" in f:
            return True, f"fact:{f[:80]}"
    return False, "missing confirmed vulnerability fact"


async def _enforce_report_finish_gate(
    state: TaskState,
    coverage_gaps: dict[str, Any],
    *,
    todo_id: str | None = None,
) -> bool:
    gate = guard_finish_phase(state)
    if gate.allow or gate.phase != Phase.VULN_SCAN:
        return False
    prev = state.current_phase
    state.force_set_phase(Phase.VULN_SCAN)
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    ctx["_report_blocked_missing_vuln_scan"] = True
    ctx["_report_blocked_missing_vuln_scan_at"] = _ts()
    state.target_context = ctx
    state.history_summary = (
        (state.history_summary or "").strip()
        + "\n[REPORT_GATE_BLOCKED] missing vuln scan coverage for web target; rerouted to VULN_SCAN"
    ).strip()
    payload: Dict[str, Any] = {
        "from_phase": prev.value,
        "to_phase": Phase.VULN_SCAN.value,
        "reason": gate.reason,
        "missing": gate.missing,
        "coverage_skill_ids": sorted(coverage_skill_ids(state)),
        "coverage_gaps": coverage_gaps,
    }
    if todo_id:
        payload["todo_id"] = todo_id
    attach_correlation(payload, state.task_id)
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="REPORT_GATE_BLOCKED",
            source_module="orchestrator",
            payload=payload,
        )
    )
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="PHASE_START",
            source_module="orchestrator",
            payload={
                "phase": Phase.VULN_SCAN.value,
                "reason": "report_gate_missing_vuln_scan",
            },
        )
    )
    return True


def _budget_target_phase(current: Phase) -> Phase:
    if current == Phase.VULN_SCAN:
        return Phase.REPORT
    return _next_phase(current) or Phase.REPORT


async def _advance_phase_with_guard(
    state: TaskState,
    target_phase: Phase,
    *,
    event_type: str,
    history_tag: str,
    payload: dict[str, Any],
    reason: str,
) -> bool:
    prev = state.current_phase
    gate = guard_next_phase(state, target_phase)
    if not gate.allow:
        blocked_event = (
            f"{event_type[: -len('_EXCEEDED')]}_BLOCKED"
            if event_type.endswith("_EXCEEDED")
            else f"{event_type}_BLOCKED"
        )
        state.history_summary = (
            (state.history_summary or "").strip()
            + f"\n[{blocked_event}] from_phase={prev.value}; requested={target_phase.value}; reason={gate.reason}"
        ).strip()
        ctx = state.target_context if isinstance(state.target_context, dict) else {}
        ctx_key = f"{event_type.lower()}_blocked"
        ctx[ctx_key] = {
            "from_phase": prev.value,
            "requested_phase": target_phase.value,
            "reason": gate.reason,
            "missing": gate.missing,
        }
        state.target_context = ctx
        blocked_payload = dict(payload)
        blocked_payload.update(
            {
                "previous_phase": prev.value,
                "requested_phase": target_phase.value,
                "reason": gate.reason,
                "missing": gate.missing,
            }
        )
        attach_correlation(blocked_payload, state.task_id)
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type=blocked_event,
                source_module="orchestrator",
                payload=blocked_payload,
            )
        )
        return False

    next_phase = gate.phase
    state.force_set_phase(next_phase)
    ok_payload = dict(payload)
    ok_payload.update(
        {
            "previous_phase": prev.value,
            "next_phase": next_phase.value,
        }
    )
    attach_correlation(ok_payload, state.task_id)
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type=event_type,
            source_module="orchestrator",
            payload=ok_payload,
        )
    )
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="PHASE_START",
            source_module="orchestrator",
            payload={"phase": next_phase.value, "reason": event_type.lower()},
        )
    )
    state.history_summary = (
        (state.history_summary or "").strip()
        + f"\n[{history_tag}] {reason} to_phase={next_phase.value}"
    ).strip()
    return True


async def _emit(event: TraceEvent) -> None:
    await emit_trace(event)


def _ts() -> str:
    return datetime.utcnow().isoformat() + "Z"


_UNKNOWN_FRAMEWORK_MARKERS_SET = frozenset({"generic_web", "custom_app", "unknown", ""})


def _detect_specific_framework_for_budget_rescue(state: TaskState) -> str:
    """
    RECON 预算熔断时的框架兜底检测：在强制跳转前尝试从已有上下文中识别具体框架，
    避免 "httpx 已检测到 weblogic/elasticsearch 但还未来得及 upgrade" 的数据丢失。
    返回具体框架名（小写）或空串。
    """
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    fw = str(ctx.get("framework_target") or ctx.get("framework_hint") or "").strip().lower()
    if fw and fw not in _UNKNOWN_FRAMEWORK_MARKERS_SET:
        return fw
    try:
        from app.core.framework_detect import detect_framework_from_context, detect_framework_from_state_fallback
        fw, _ = detect_framework_from_context(ctx)
        if not fw:
            fw, _ = detect_framework_from_state_fallback(state)
        fw = (fw or "").strip().lower()
        if fw and fw not in _UNKNOWN_FRAMEWORK_MARKERS_SET:
            return fw
    except Exception:
        pass
    return ""


async def _maybe_enforce_phase_wall_clock_budget(state: TaskState) -> None:
    """
    阶段墙钟熔断：超限时通过 phase guard 推进，不能绕过阶段退出条件。
    上限优先 TaskState.current_phase_duration_limit_sec，否则读 ORCH_PHASE_WALL_CLOCK_LIMIT_SEC。

    RECON 例外：若熔断时已检测到具体框架（weblogic / elasticsearch 等），跳转到 VULN_SCAN
    而非 THREAT_MODEL，确保后续漏洞扫描能够拿到框架线索。
    """
    if state.current_phase in (Phase.REPORT, Phase.DONE):
        return
    if state.status != TaskStatus.RUNNING:
        return
    limit = state.current_phase_duration_limit_sec
    if limit is None:
        limit = phase_wall_clock_limit_sec_from_env()
    if limit is None or state.phase_start_at is None:
        return
    elapsed = (datetime.utcnow() - state.phase_start_at).total_seconds()
    if elapsed <= float(limit):
        return
    prev = state.current_phase
    next_forced_phase = _budget_target_phase(prev)
    rescued_framework: str = ""
    if prev == Phase.RECON:
        rescued_framework = _detect_specific_framework_for_budget_rescue(state)
        if rescued_framework:
            next_forced_phase = Phase.VULN_SCAN
            ctx = state.target_context if isinstance(state.target_context, dict) else {}
            ctx["framework_target"] = rescued_framework
            ctx["framework_hint"] = rescued_framework
            state.target_context = ctx
    reason = (
        f"elapsed_sec={int(elapsed)} limit_sec={int(limit)} from_phase={prev.value}"
        + (f" rescued_framework={rescued_framework}" if rescued_framework else "")
    )
    pl: Dict[str, Any] = {
        "elapsed_sec": elapsed,
        "limit_sec": limit,
    }
    if rescued_framework:
        pl["rescued_framework"] = rescued_framework
    await _advance_phase_with_guard(
        state,
        next_forced_phase,
        event_type="PHASE_BUDGET_EXCEEDED",
        history_tag="PHASE_BUDGET_EXCEEDED",
        payload=pl,
        reason=reason,
    )


async def _maybe_enforce_phase_cost_budget(state: TaskState) -> None:
    """
    FinOps 熔断：累计成本超过 ORCH_PHASE_COST_BUDGET_USD 时通过 phase guard 推进。
    与墙钟熔断并列；未配置预算时不生效。
    """
    from app.core.governance_cost import phase_cost_budget_usd_from_env

    budget = phase_cost_budget_usd_from_env()
    if budget is None:
        return
    if state.current_phase in (Phase.REPORT, Phase.DONE):
        return
    if state.status != TaskStatus.RUNNING:
        return
    total = float(state.cumulative_cost_usd or 0.0)
    if total <= float(budget):
        return
    prev = state.current_phase
    target_phase = _budget_target_phase(prev)
    reason = f"cumulative_cost_usd={total:.8f} budget_usd={float(budget):.8f} from_phase={prev.value}"
    pl: Dict[str, Any] = {
        "cumulative_cost_usd": total,
        "budget_usd": float(budget),
    }
    await _advance_phase_with_guard(
        state,
        target_phase,
        event_type="COST_BUDGET_EXCEEDED",
        history_tag="COST_BUDGET_EXCEEDED",
        payload=pl,
        reason=reason,
    )


async def _maybe_enforce_phase_plan_round_cap(state: TaskState) -> None:
    """
    Plan 决策轮数硬上限：同一阶段内 plan_list_decision 次数超过
    ORCH_MAX_PLAN_ROUNDS_PER_PHASE（默认 10）时，强行推进到下一阶段或 REPORT。
    弥补 loop_guard 只检测「相同签名重复」的盲点——LLM 换不同 skill 组合时 loop_guard 不触发，
    但阶段仍在无限循环。
    """
    cap = max_plan_rounds_per_phase_from_env()
    if cap <= 0:
        return
    if state.current_phase in (Phase.REPORT, Phase.DONE):
        return
    if state.status != TaskStatus.RUNNING:
        return
    if state.phase_plan_round_count < cap:
        return
    prev = state.current_phase
    rounds_hit = state.phase_plan_round_count
    target_phase = _budget_target_phase(prev)
    reason = (
        f"plan_rounds={rounds_hit} cap={cap} "
        f"from_phase={prev.value} to_phase={target_phase.value}"
    )
    pl: Dict[str, Any] = {
        "plan_rounds": rounds_hit,
        "cap": cap,
    }
    await _advance_phase_with_guard(
        state,
        target_phase,
        event_type="PLAN_ROUND_CAP_EXCEEDED",
        history_tag="PLAN_ROUND_CAP_EXCEEDED",
        payload=pl,
        reason=reason,
    )


def _accumulate_finops_from_exec(
    state: TaskState,
    exec_result: Any,
    resolved_artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    """从单次执行结果解析 usage 并累加到 TaskState；返回可写入 Trace 的摘要（无密钥）。"""
    from app.core.governance_cost import accumulate_finops_usage_mapping_into_state, merge_usage_sources

    usage = merge_usage_sources(exec_result, resolved_artifacts)
    return accumulate_finops_usage_mapping_into_state(state, usage)


async def _accumulate_finops_from_orch_llm(
    state: TaskState,
    usage: Dict[str, Any] | None,
) -> None:
    """编排侧决策/摘要等 LLM 调用的 token 并入 FinOps 累计。"""
    from app.core.governance_cost import accumulate_finops_usage_mapping_into_state

    delta = accumulate_finops_usage_mapping_into_state(state, usage)
    if not delta:
        return
    await _maybe_enforce_phase_cost_budget(state)
    pl: Dict[str, Any] = {
        "phase": state.current_phase.value,
        "source": "orchestrator_llm",
        "finops_delta": delta,
    }
    attach_correlation(pl, state.task_id)
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="ORCH_LLM_FINOPS",
            source_module="orchestrator",
            payload=pl,
        )
    )


async def _apply_plan_orchestration_phase_advance(
    state: TaskState,
    plan_list: PlanList,
    *,
    todo_id: str | None = None,
) -> None:
    orch = plan_list.orchestration
    if not orch or not orch.advance_phase:
        return
    raw_next = (orch.next_phase or "").strip()
    if not raw_next:
        return
    try:
        desired = coerce_phase_from_llm(raw_next)
    except ValueError as exc:
        rej: Dict[str, Any] = {"error": str(exc), "raw_next_phase": raw_next}
        attach_correlation(rej, state.task_id)
        if todo_id:
            rej["todo_id"] = todo_id
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PLAN_ORCHESTRATION_PHASE_REJECT",
                source_module="orchestrator",
                payload=rej,
            )
        )
        state.history_summary = (
            (state.history_summary or "").strip() + f"\n[PLAN_ORCHESTRATION_REJECT] invalid next_phase={raw_next!r}"
        ).strip()
        return
    if desired == state.current_phase:
        return
    gate = guard_next_phase(state, desired)
    if gate.allow:
        prev = state.current_phase
        state.set_phase(gate.phase)
        pl2: Dict[str, Any] = {
            "from_phase": prev.value,
            "to_phase": gate.phase.value,
            "reason": orch.reason or "",
        }
        attach_correlation(pl2, state.task_id)
        if todo_id:
            pl2["todo_id"] = todo_id
        try:
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="PHASE_END",
                    source_module="orchestrator",
                    payload={"phase": prev.value, "reason": "plan_orchestration"},
                )
            )
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="PHASE_START",
                    source_module="orchestrator",
                    payload={"phase": gate.phase.value, "reason": "plan_orchestration"},
                )
            )
        except Exception:
            logger.warning(
                "plan orchestration trace emit failed task_id=%s (phase already advanced in memory)",
                state.task_id,
                exc_info=True,
            )
        state.history_summary = (
            (state.history_summary or "").strip()
            + f"\n[PHASE_ADVANCE] {prev.value} -> {gate.phase.value} ({orch.reason or 'planner orchestration'})"
        ).strip()
    else:
        await _emit_phase_gate_blocked(
            state,
            requested_phase=desired.value,
            reason=f"plan_orchestration: {gate.reason}",
            missing=gate.missing or ["phase_transition_guard"],
            todo_id=todo_id,
        )


async def _handle_plan_list_decision_path(
    state: TaskState,
    plan_list: PlanList,
    *,
    todo_id: str | None = None,
) -> None:
    """R2b：将 Planner 输出的 PlanList 写入 target_context（checkpoint 随存）并发 trace。"""
    state.target_context.pop(PLAN_LIST_VALIDATION_ERROR_CONTEXT_KEY, None)
    state.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = plan_list.model_dump(mode="json")
    state.target_context["_latest_plan_list_saved_at"] = _ts()
    state.target_context[PLAN_DISPATCH_NEXT_INDEX_KEY] = 0
    items = plan_list.items or []
    n_items = len(items)
    skill_ids = [it.skill_id for it in items][:32]
    payload: Dict[str, Any] = {
        "phase": state.current_phase.value,
        "plan_task_id": plan_list.task_id,
        "batch_id": plan_list.batch_id,
        "item_count": n_items,
        "skill_ids": skill_ids,
        "orch_plan_mode_enabled": True,
    }
    if todo_id:
        payload["todo_id"] = todo_id
    attach_correlation(payload, state.task_id)
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="PLAN_LIST_DECISION",
            source_module="orchestrator",
            payload=payload,
        )
    )
    note = (
        f"\n[PLAN_LIST] Persisted {n_items} plan item(s) "
        f"(batch_id={plan_list.batch_id!r}). Awaiting compile/dispatch pipeline."
    )
    if todo_id:
        note += f" todo_id={todo_id}."
    state.history_summary = ((state.history_summary or "").strip() + note).strip()
    state.phase_plan_round_count += 1
    state.updated_at = datetime.utcnow()
    await _apply_plan_orchestration_phase_advance(state, plan_list, todo_id=todo_id)


def _host_of(s: str) -> str:
    """从 URL 或 host:port 中提取 host。"""
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
        return _SCOPE_LOCALHOST_ALIAS.lower() if _SCOPE_LOCALHOST_ALIAS else "host.docker.internal"
    return h


def _explicit_port_of_target(target: str) -> int | None:
    s = (target or "").strip()
    if not s:
        return None
    if "://" in s:
        try:
            parsed = urlparse(s)
            return parsed.port
        except Exception:
            return None
    if ":" in s:
        maybe = s.rsplit(":", 1)[-1]
        if maybe.isdigit():
            return int(maybe)
    return None


def _enforce_single_port_scope_for_nmap(params: dict[str, Any], port: int) -> dict[str, Any]:
    patched = dict(params or {})
    patched["ports"] = str(port)
    for key in ("top_ports",):
        if key in patched:
            patched.pop(key, None)

    def _drop_port_switches(raw: str) -> str:
        kept: list[str] = []
        tokens = shlex.split(raw)
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in ("-p", "--top-ports", "-p-"):
                # skip current token and its value (if any)
                if t in ("-p", "--top-ports") and i + 1 < len(tokens):
                    i += 2
                else:
                    i += 1
                continue
            if t.startswith("-p") and t != "-Pn":
                i += 1
                continue
            kept.append(t)
            i += 1
        return " ".join(kept)

    for key in ("args", "arguments"):
        raw = patched.get(key)
        if isinstance(raw, str) and raw.strip():
            patched[key] = _drop_port_switches(raw)
    return patched


def _is_target_allowed(target: str, allowed_target: str | None, skill_id: str) -> bool:
    """
    与执行器保持一致的基础范围校验：
    - 未配置 allowed_target 时放行
    - 搜索类 skill 放行（target 可能是查询串）
    - 其余场景要求 target 与 allowed_target 同 host
    """
    if not allowed_target or not allowed_target.strip():
        return True
    if (skill_id or "").lower() in ("baidu-search", "web_search", "search"):
        return True
    return _normalize_scope_host(_host_of(target)) == _normalize_scope_host(_host_of(allowed_target))


_PHASE_ORDER = [
    Phase.RECON,
    Phase.THREAT_MODEL,
    Phase.VULN_SCAN,
    Phase.EXPLOIT,
    Phase.REPORT,
]


def _next_phase(current: Phase) -> Phase | None:
    try:
        idx = _PHASE_ORDER.index(current)
    except ValueError:
        return None
    return _PHASE_ORDER[idx + 1] if idx + 1 < len(_PHASE_ORDER) else None


def _resolve_skill_id_alias(skill_id: str | None, available_skills: List[str]) -> str:
    raw = (skill_id or "").strip()
    if not raw:
        return ""
    if raw in available_skills:
        return raw
    mapped = _SKILL_ID_ALIASES.get(raw.lower(), "")
    if mapped and mapped in available_skills:
        return mapped
    return raw


def _normalize_framework_from_context(ctx: dict[str, Any]) -> tuple[str, list[str]]:
    """
    THREAT_MODEL 硬规则：从技术证据和可疑端点中归一框架标签。
    委托给 framework_detect.detect_framework_from_context（强/弱信号分离版本）。
    保留此函数签名以维持对旧调用方的兼容。
    """
    from app.core.framework_detect import detect_framework_from_context
    return detect_framework_from_context(ctx)


def _infer_framework_from_state_fallback(state: TaskState) -> tuple[str, list[str]]:
    """
    兜底推断：当黑板未明确 framework_target 时，从 confirmed_facts/history_summary 等自然语言证据补推断。
    委托给 framework_detect.detect_framework_from_state_fallback（含 jenkins/elasticsearch/solr/weblogic）。
    """
    from app.core.framework_detect import detect_framework_from_state_fallback
    return detect_framework_from_state_fallback(state)


async def _apply_threat_model_framework_hard_rule(state: TaskState, *, todo_id: str | None = None) -> None:
    if state.current_phase != Phase.THREAT_MODEL:
        return
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    # Fix D5：改为调用共享 detector，保持与 RECON→VULN_SCAN 直跳通道一致。
    # 原 _normalize_framework_from_context / _infer_framework_from_state_fallback
    # 仅供向后兼容（防止其他 import 此符号的调用点炸掉），内部走相同逻辑。
    from app.core.framework_detect import (
        detect_framework_from_context,
        detect_framework_from_state_fallback,
    )
    fw, evidence_hits = detect_framework_from_context(ctx)
    if not fw:
        fw, e2 = detect_framework_from_state_fallback(state)
        evidence_hits.extend(e2)
    if not fw:
        return
    prev = str(ctx.get("framework_target") or "").strip().lower()
    if prev == fw:
        return
    ctx["framework_target"] = fw
    ctx["framework_hint"] = fw
    payload: Dict[str, Any] = {
        "phase": state.current_phase.value,
        "framework_target": fw,
        "evidence_hits": evidence_hits[:20],
    }
    if todo_id:
        payload["todo_id"] = todo_id
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="FRAMEWORK_TARGET_SET",
            source_module="orchestrator",
            payload=payload,
        )
    )


_UNKNOWN_FRAMEWORK_MARKER_LOWER = frozenset({"generic_web", "custom_app", "unknown", ""})


async def _maybe_upgrade_framework_target(state: TaskState) -> None:
    """
    Fix D6：当 framework_target 停留在 GENERIC_WEB / CUSTOM_APP 等通用 marker 时，
    重新跑框架识别；若 VULN_SCAN / EXPLOIT 累积出的 tech_stack_evidence 包含具体
    框架关键词，就把 marker 升级为具体框架（struts2 / spring / thinkphp / php 等）。

    只升级（marker → specific），不降级（specific → marker），避免 evidence 波动导致 flapping。
    升级时向 trace 发 FRAMEWORK_TARGET_UPGRADED 事件便于观测。
    """
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    if not isinstance(ctx, dict):
        return
    current = str(ctx.get("framework_target") or "").strip().lower()
    if current not in _UNKNOWN_FRAMEWORK_MARKER_LOWER:
        return  # 已有具体框架，保持不变
    from app.core.framework_detect import (
        detect_framework_from_context,
        detect_framework_from_state_fallback,
    )
    fw, evidence = detect_framework_from_context(ctx)
    if not fw:
        fw, e2 = detect_framework_from_state_fallback(state)
        evidence.extend(e2)
    if not fw:
        return
    prev = ctx.get("framework_target")
    ctx["framework_target"] = fw
    ctx["framework_hint"] = fw
    payload: Dict[str, Any] = {
        "phase": state.current_phase.value,
        "from": prev,
        "to": fw,
        "evidence_hits": evidence[:20],
    }
    attach_correlation(payload, state.task_id)
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="FRAMEWORK_TARGET_UPGRADED",
            source_module="orchestrator",
            payload=payload,
        )
    )


async def _emit_phase_gate_blocked(
    state: TaskState,
    *,
    requested_phase: str,
    reason: str,
    missing: list[str],
    todo_id: str | None = None,
) -> None:
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    sig = f"{state.current_phase.value}|{requested_phase}|{reason}|{','.join(missing)}"
    last_sig = str(ctx.get("_phase_gate_blocked_last_sig") or "").strip()
    if sig == last_sig:
        try:
            ctx["_phase_gate_blocked_suppressed_count"] = int(ctx.get("_phase_gate_blocked_suppressed_count") or 0) + 1
        except Exception:
            ctx["_phase_gate_blocked_suppressed_count"] = 1
        state.target_context = ctx
        return
    ctx["_phase_gate_blocked_last_sig"] = sig
    state.target_context = ctx

    payload: Dict[str, Any] = {
        "from_phase": state.current_phase.value,
        "requested_phase": requested_phase,
        "reason": reason,
        "missing_requirements": missing,
    }
    if todo_id:
        payload["todo_id"] = todo_id
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="PHASE_GATE_BLOCKED",
            source_module="orchestrator",
            payload=payload,
        )
    )
    state.history_summary = (
        (state.history_summary or "").strip()
        + f"\n[PHASE_GATE_BLOCKED] requested={requested_phase}; reason={reason}; missing={','.join(missing)}"
    )


async def _inject_threat_model_framework_requirements(
    state: TaskState,
    available_skills: list[str],
    *,
    todo_id: str | None = None,
) -> bool:
    if state.current_phase != Phase.THREAT_MODEL:
        return False
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    # 未完成框架标定时，仅注入强制事实约束与系统反馈；具体工具由 LLM 自主选择。
    if str(ctx.get("framework_target") or "").strip():
        return False
    if bool(ctx.get("_threat_model_framework_requirements_injected")):
        return False

    suggested = [s for s in available_skills if s in ("whatweb-fingerprint", "http-enum", "katana", "dirsearch")]
    ctx["threat_model_required_facts"] = {
        "must_set_framework_target": True,
        "allow_unknown_markers": ["GENERIC_WEB", "CUSTOM_APP"],
        "suggested_discovery_skills": suggested[:8],
    }
    ctx["_threat_model_framework_requirements_injected"] = True
    state.target_context = ctx
    state.history_summary = (
        (state.history_summary or "").strip()
        + "\n[SYSTEM_CONSTRAINT] THREAT_MODEL requires framework_target before NEXT_PHASE. "
        "Use available skills to gather stack evidence; if exhausted, set explicit unknown marker "
        "(GENERIC_WEB or CUSTOM_APP) with evidence instead of leaving framework_target empty."
    )
    payload: Dict[str, Any] = {
        "phase": state.current_phase.value,
        "reason": "framework_target missing; enforce facts-first decisioning",
        "required_facts": ["framework_target"],
        "suggested_skills": suggested[:8],
    }
    if todo_id:
        payload["todo_id"] = todo_id
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="THREAT_MODEL_FACTS_REQUIRED",
            source_module="orchestrator",
            payload=payload,
        )
    )
    return True


def _sanitize_execute_actions(
    actions: List[ActionItem] | None,
    available_skills: List[str],
) -> tuple[List[ActionItem], List[str], List[Dict[str, str]]]:
    sanitized: List[ActionItem] = []
    invalid: List[str] = []
    alias_mapped: List[Dict[str, str]] = []
    for action in (actions or []):
        raw = str(getattr(action, "skill_id", "") or "").strip()
        resolved = _resolve_skill_id_alias(raw, available_skills)
        if raw and raw != resolved:
            alias_mapped.append({"from": raw, "to": resolved})
        if resolved and resolved in available_skills:
            sanitized.append(ActionItem(skill_id=resolved, params=dict(getattr(action, "params", None) or {})))
        elif raw:
            invalid.append(raw)
    return sanitized, invalid, alias_mapped


async def _apply_decision_memory_updates(
    state: TaskState,
    decision: Any,
    *,
    task_store: TaskStore | None = None,
    current_todo: Any = None,
) -> None:
    added, removed = apply_fact_updates(
        state,
        list(getattr(decision, "facts_to_add", None) or []),
        list(getattr(decision, "facts_to_remove", None) or []),
    )
    if added:
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="MEMORY_FACT_ADDED",
                source_module="orchestrator",
                payload={"phase": state.current_phase.value, "facts": added[:20]},
            )
        )
    if removed:
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="MEMORY_FACT_REMOVED",
                source_module="orchestrator",
                payload={"phase": state.current_phase.value, "facts": removed[:20]},
            )
        )
    todo_update = getattr(decision, "todo_status_update", None)
    if todo_update and task_store and current_todo:
        new_status = str(getattr(todo_update, "status", "") or "").strip().upper()
        if new_status in (TodoStatus.PENDING.value, TodoStatus.RUNNING.value, TodoStatus.DONE.value, TodoStatus.FAILED.value):
            set_todo_status(state, current_todo.id, TodoStatus(new_status))
            await task_store.update_todo_status(state.task_id, current_todo.id, new_status)
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="TODO_STATUS_UPDATED",
                    source_module="orchestrator",
                    payload={
                        "todo_id": current_todo.id,
                        "status": new_status,
                        "reason": getattr(todo_update, "reason", None),
                    },
                )
            )


def _dedup_actions_by_memory(state: TaskState, actions: list[ActionItem]) -> tuple[list[ActionItem], list[dict[str, Any]]]:
    kept: list[ActionItem] = []
    dropped: list[dict[str, Any]] = []
    for action in actions or []:
        params = dict(getattr(action, "params", None) or {})
        target = str(params.get("target") or state.target)
        sig = build_action_signature(str(getattr(action, "skill_id", "") or ""), target, params)
        if detect_repeated_signature(state, sig, lookback=8):
            dropped.append(
                {
                    "skill_id": str(getattr(action, "skill_id", "") or ""),
                    "target": target,
                    "action_signature": sig,
                }
            )
            continue
        kept.append(action)
    return kept, dropped


def _inject_memory_views_into_context(state: TaskState) -> None:
    if state.target_context is None:
        state.target_context = {}
    state.target_context["confirmed_facts"] = list(getattr(state, "confirmed_facts", []) or [])
    state.target_context["entity_blackboard"] = dict(getattr(state, "entity_blackboard", {}) or {})
    state.target_context["fallback_baseline"] = dict(getattr(state, "fallback_baseline", {}) or {})
    state.target_context["action_ledger_recent"] = list(getattr(state, "action_ledger", []) or [])[-12:]


async def _run_actions_and_merge_results(
    state: TaskState,
    actions: List[ActionItem],
    available_skills: List[str],
    enable_executor: bool,
    *,
    current_todo: Any = None,
    task_store: Optional[TaskStore] = None,
    plan_id_for_invocation: str | None = None,
) -> dict[str, str]:
    """
    多任务并发派发与回收：遍历 decision.actions，并发 dispatch_only，顺序 apply，最后统一结算 loop_guard。
    用于 EXECUTE_SKILLS 分支（无 Todo 与带 Todo 两路复用）。
    """
    action_list: List[tuple[ActionItem, str]] = []
    for action in (actions or []):
        resolved_skill_id = _resolve_skill_id_alias(getattr(action, "skill_id", None), available_skills)
        if resolved_skill_id and resolved_skill_id in available_skills:
            action_list.append((action, resolved_skill_id))
    if not action_list:
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="DECISION_SANITIZED",
                source_module="orchestrator",
                payload={
                    "phase": state.current_phase.value,
                    "reason": "no executable action after sanitize",
                    "requested_skill_ids": [str(getattr(a, "skill_id", "") or "") for a in (actions or [])],
                    "available_skills": available_skills,
                },
            )
        )
        return {}

    run_id_ctx = str((state.target_context or {}).get("run_id") or "").strip()
    if not run_id_ctx:
        seed = f"{state.task_id}|{state.target}|discovery"
        run_id_ctx = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
        state.target_context["run_id"] = run_id_ctx
    shared_discovery_dir = _shared_target_discovery_dir(state.task_id, run_id_ctx)
    effective_status_by_skill: dict[str, str] = {}

    skill_executor = SkillExecutor()
    call_ctxs: List[SkillCallContext] = []
    call_started_ts: List[str] = []
    call_started_perf: List[float] = []
    for item, resolved_skill_id in action_list:
        params = dict(getattr(item, "params", None) or {})
        raw_target = str(params.get("target") or state.target)
        target = raw_target
        target_port = _explicit_port_of_target(state.target)
        if not _is_target_allowed(raw_target, state.target, resolved_skill_id):
            target = state.target
            params["target"] = target
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="TARGET_ADJUSTED",
                    source_module="orchestrator",
                    payload={
                        "skill_id": resolved_skill_id,
                        "requested_target": raw_target,
                        "adjusted_target": target,
                        "reason": "target out of authorized scope; fallback to task target",
                    },
                )
            )
        if target_port and (resolved_skill_id or "").lower() == "nmap":
            params = _enforce_single_port_scope_for_nmap(params, target_port)

        # Target-Centric Shared Workspace 输出目录强制约束：
        # - katana/dirsearch：写入固定共享 discovery 目录
        # - dispatcher：读取同一份 discovery 输入
        sl = (resolved_skill_id or "").strip().lower()
        if sl == "katana":
            params.setdefault("output_dir", shared_discovery_dir)
            # Katana 内置 dirsearch 默认跳过，避免覆盖外部 dirsearch 的共享产物
            params.setdefault("skip_dirsearch", True)
        elif sl == "dirsearch":
            params.setdefault("output_dir", shared_discovery_dir)
        elif sl == "dispatcher":
            params.setdefault("output_discovery_dir", shared_discovery_dir)
        elif sl == "nuclei":
            params.setdefault("output_discovery_dir", shared_discovery_dir)
            fw = str((state.target_context or {}).get("framework_target") or (state.target_context or {}).get("framework_hint") or "").strip()
            if not fw:
                fw, _ev = _infer_framework_from_state_fallback(state)
            if fw:
                params.setdefault("framework_hint", fw)
        elif sl == "read_target_list":
            params["rel_path"] = _resolve_read_target_list_rel_path(
                task_id=state.task_id,
                params=params,
                target_context=state.target_context,
                shared_discovery_dir=shared_discovery_dir,
            )
        call_ctx = SkillCallContext(
            task_id=state.task_id,
            phase=state.current_phase,
            skill_id=resolved_skill_id,
            target=target,
            params=params,
            allowed_target=state.target,
            todo_id=current_todo.id if current_todo else None,
            agent_role="manager_executor" if current_todo else None,
            request_id=f"req-{uuid.uuid4().hex}",
            plan_id=plan_id_for_invocation,
        )
        call_ctxs.append(call_ctx)
        started_ts = _ts()
        call_started_ts.append(started_ts)
        call_started_perf.append(time.perf_counter())

        skill_invoked_payload: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "skill_id": resolved_skill_id,
            "target": target,
            "params": params,
            "request_id": call_ctx.request_id,
        }
        if call_ctx.agent_role:
            skill_invoked_payload["agent_role"] = call_ctx.agent_role
        if call_ctx.todo_id:
            skill_invoked_payload["todo_id"] = call_ctx.todo_id
        attach_correlation(
            skill_invoked_payload,
            state.task_id,
            request_id=call_ctx.request_id,
            plan_id=call_ctx.plan_id,
        )
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=started_ts,
                event_type="SKILL_INVOKED",
                source_module="orchestrator",
                payload=skill_invoked_payload,
                run_started_at=started_ts,
            )
        )
        executor_request_payload: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "skill_id": resolved_skill_id,
            "target": target,
            "params": params,
            "request_id": call_ctx.request_id,
            "allowed_target": state.target,
        }
        if call_ctx.agent_role:
            executor_request_payload["agent_role"] = call_ctx.agent_role
        if call_ctx.todo_id:
            executor_request_payload["todo_id"] = call_ctx.todo_id
        attach_correlation(
            executor_request_payload,
            state.task_id,
            request_id=call_ctx.request_id,
            plan_id=call_ctx.plan_id,
        )
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=started_ts,
                event_type="EXECUTOR_REQUEST",
                source_module="orchestrator",
                payload=executor_request_payload,
                run_started_at=started_ts,
            )
        )

    if not enable_executor:
        for ctx in call_ctxs:
            skipped: Dict[str, Any] = {
                "phase": state.current_phase.value,
                "skill_id": ctx.skill_id,
                "target": ctx.target,
                "status": "SKIPPED_EXECUTOR_DISABLED",
                "parsed_artifacts": {},
            }
            if ctx.agent_role:
                skipped["agent_role"] = ctx.agent_role
            if ctx.todo_id:
                skipped["todo_id"] = ctx.todo_id
            attach_correlation(
                skipped,
                state.task_id,
                request_id=ctx.request_id,
                plan_id=ctx.plan_id,
            )
            skipped["structured_error"] = {
                "kind": "skill_dispatch",
                "code": "SKIPPED_EXECUTOR_DISABLED",
                "message": "executor disabled; skill not run",
                "details": {"skill_id": ctx.skill_id},
            }
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="SKILL_COMPLETED",
                    source_module="orchestrator",
                    payload=skipped,
                )
            )
            state.coverage_attempted.append(
                {
                    "target": ctx.target,
                    "skill_id": ctx.skill_id,
                    "status": "SKIPPED_EXECUTOR_DISABLED",
                }
            )
        if current_todo and task_store:
            set_todo_status(state, current_todo.id, TodoStatus.DONE)
            await task_store.update_todo_status(state.task_id, current_todo.id, TodoStatus.DONE.value)
        state.target_context["_coverage_attempted"] = state.coverage_attempted
        state.updated_at = datetime.utcnow()
        return {}

    tasks = [
        skill_executor.execute_skill_dispatch_only(
            state=state,
            call_ctx=ctx,
            enable_executor=True,
            available_skills=available_skills,
        )
        for ctx in call_ctxs
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    loop_break_triggered = False
    last_loop_break_skill: Optional[str] = None
    for i, ctx in enumerate(call_ctxs):
        finished_ts = _ts()
        duration_ms = int((time.perf_counter() - call_started_perf[i]) * 1000) if i < len(call_started_perf) else None
        raw = results[i] if i < len(results) else None
        if isinstance(raw, Exception):
            logger.warning("action %s skill_id=%s failed: %s", i, ctx.skill_id, raw)
            payload = {
                "exec_result": None,
                "resolved_artifacts": {"error": str(raw)},
                "artifact_dir": "",
            }
        else:
            payload = raw or {}
        applied = await skill_executor.apply_execution_result(state, ctx, payload)
        try:
            er = applied.get("exec_result")
            req_id = str((er.request_id if er else "") or "").strip()
            if req_id:
                ctx.request_id = req_id
        except Exception:
            pass
        resolved_artifacts = applied.get("resolved_artifacts") or {}
        try:
            latest_ledger = list(getattr(state, "action_ledger", []) or [])[-1]
        except Exception:
            latest_ledger = {}
        if not bool(applied.get("skip_memory_blackboard_update")):
            mb_payload: Dict[str, Any] = {
                "phase": state.current_phase.value,
                "skill_id": ctx.skill_id,
                "target": ctx.target,
                "ledger_signature": (latest_ledger or {}).get("action_signature"),
            }
            attach_correlation(mb_payload, state.task_id, request_id=ctx.request_id, plan_id=ctx.plan_id)
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="MEMORY_BLACKBOARD_UPDATED",
                    source_module="orchestrator",
                    payload=mb_payload,
                )
            )
        effective_status = "unknown"
        if applied.get("exec_result"):
            effective_status = applied["exec_result"].status or "unknown"
        effective_status_by_skill[str(ctx.skill_id or "").strip().lower()] = str(effective_status or "").strip().upper()
        skill_completed_payload: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "skill_id": ctx.skill_id,
            "target": ctx.target,
            "status": effective_status,
            "parsed_artifacts": resolved_artifacts,
            "artifact_dir": applied.get("artifact_dir", ""),
            "request_id": ctx.request_id,
        }
        fin_delta = _accumulate_finops_from_exec(
            state,
            applied.get("exec_result"),
            resolved_artifacts if isinstance(resolved_artifacts, dict) else {},
        )
        if fin_delta:
            skill_completed_payload["finops_delta"] = fin_delta
        if ctx.agent_role:
            skill_completed_payload["agent_role"] = ctx.agent_role
        if ctx.todo_id:
            skill_completed_payload["todo_id"] = ctx.todo_id
        attach_correlation(
            skill_completed_payload,
            state.task_id,
            request_id=ctx.request_id,
            plan_id=ctx.plan_id,
        )
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=finished_ts,
                event_type="SKILL_COMPLETED",
                source_module="orchestrator",
                payload=skill_completed_payload,
                run_started_at=call_started_ts[i] if i < len(call_started_ts) else None,
                run_finished_at=finished_ts,
                run_duration_ms=duration_ms if duration_ms is not None and duration_ms >= 0 else None,
            )
        )
        exec_result = applied.get("exec_result")
        executor_response_payload: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "skill_id": ctx.skill_id,
            "target": ctx.target,
            "request_id": ctx.request_id,
            "status": effective_status,
            "duration_ms": (exec_result.duration_ms if exec_result else None),
            "artifact_ref": str((resolved_artifacts or {}).get("_artifact_ref") or ""),
            "parsed_artifacts_preview": json.dumps(resolved_artifacts or {}, ensure_ascii=False)[:1200],
        }
        if ctx.agent_role:
            executor_response_payload["agent_role"] = ctx.agent_role
        if ctx.todo_id:
            executor_response_payload["todo_id"] = ctx.todo_id
        attach_correlation(
            executor_response_payload,
            state.task_id,
            request_id=ctx.request_id,
            plan_id=ctx.plan_id,
        )
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=finished_ts,
                event_type="EXECUTOR_RESPONSE",
                source_module="orchestrator",
                payload=executor_response_payload,
                run_started_at=call_started_ts[i] if i < len(call_started_ts) else None,
                run_finished_at=finished_ts,
                run_duration_ms=duration_ms if duration_ms is not None and duration_ms >= 0 else None,
            )
        )
        state.coverage_attempted.append(
            {
                "target": ctx.target,
                "skill_id": ctx.skill_id,
                "status": str(effective_status or "").strip() or "unknown",
            }
        )
        if str(ctx.skill_id or "").strip().lower() == "nuclei" and isinstance(resolved_artifacts, dict):
            _auto_mark_exploit_ready_from_nuclei(state, resolved_artifacts)
        if getattr(state, "recent_summary_chunks", None) is None:
            state.recent_summary_chunks = []
        try:
            from app.clients.llm_client import call_post_run_summary
            snippet = json.dumps(resolved_artifacts or {}, ensure_ascii=False)[:2000]
            summary, sum_usage = await call_post_run_summary(
                state.current_phase.value, ctx.skill_id, ctx.target, snippet
            )
            await _accumulate_finops_from_orch_llm(state, sum_usage)
            if summary:
                state.recent_summary_chunks.append(summary)
                if len(state.recent_summary_chunks) > 20:
                    state.recent_summary_chunks = state.recent_summary_chunks[-20:]
        except Exception:
            pass
        loop_signature = "|".join(
            [
                str(ctx.skill_id or ""),
                str(ctx.target or ""),
                canonical_params(ctx.params),
                artifact_hash(resolved_artifacts),
            ]
        )
        if update_loop_guard(state, loop_signature):
            loop_break_triggered = True
            last_loop_break_skill = ctx.skill_id

    await _maybe_enforce_phase_cost_budget(state)
    await _maybe_enforce_phase_plan_round_cap(state)

    state.target_context["_coverage_attempted"] = state.coverage_attempted

    if current_todo and task_store:
        set_todo_status(state, current_todo.id, TodoStatus.DONE)
        await task_store.update_todo_status(state.task_id, current_todo.id, TodoStatus.DONE.value)

    if loop_break_triggered and last_loop_break_skill:
        alternatives = [sid for sid in available_skills if sid != last_loop_break_skill]
        # EXPLOIT 阶段确权已落实时，LOOP_BREAK 直接强推到 REPORT，不再留给 LLM 自选 alternatives。
        # 实地证据：task-9d0e494a... 在 exploit_ready=True / confirmed_cve=['S2-057','S2-045'] 的
        # 前提下，LOOP_BREAK 只写 avoid_skill=dispatcher 的 hint；LLM 把 dispatcher 换成
        # nuclei/read_workspace_artifact 继续循环，共累计 12 次 EXPLOIT 派发未推进 REPORT。
        exploit_force_report = (
            state.current_phase == Phase.EXPLOIT
            and _has_confirmed_vuln_for_exploit(state)[0]
        )
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="LOOP_BREAK",
                source_module="orchestrator",
                payload={
                    "phase": state.current_phase.value,
                    "repeated_skill": last_loop_break_skill,
                    "repeat_count": state.loop_repeat_count,
                    "alternatives": alternatives,
                    "exploit_force_report": exploit_force_report,
                },
            )
        )
        if alternatives and not exploit_force_report:
            # 只写 avoid_skill，不写 prefer_skill：
            # 已在 task-55cec339... 发现 prefer_skill=alternatives[0] 是武断选择，
            # 会把 LLM 推入另一条无意义循环（例如被引导到 curl-raw 后以 {target,timeout}
            # 连发 12 次打根路径，触发二次 LOOP_BREAK 又弹回 read_*，形成"弹球")。
            # 留白让 LLM 依据上下文自主选择，并继续在 decision_context 中看到可用技能清单。
            state.target_context["loop_break_hint"] = {
                "avoid_skill": last_loop_break_skill,
                "reason": "repeated execution produced no new artifacts",
            }
            state.history_summary += (
                f"\n[LOOP_BREAK] avoid={last_loop_break_skill}, "
                f"phase={state.current_phase.value}"
            )
        else:
            nxt = _next_phase(state.current_phase)
            if nxt:
                gate = guard_next_phase(state, nxt)
                if gate.allow:
                    prev = state.current_phase
                    await _emit(
                        TraceEvent(
                            task_id=state.task_id,
                            timestamp=_ts(),
                            event_type="PHASE_END",
                            source_module="orchestrator",
                            payload={"phase": prev.value},
                        )
                    )
                    state.set_phase(gate.phase)
                    await _emit(
                        TraceEvent(
                            task_id=state.task_id,
                            timestamp=_ts(),
                            event_type="PHASE_START",
                            source_module="orchestrator",
                            payload={"phase": gate.phase.value, "reason": "loop break fallback"},
                        )
                    )
                    state.history_summary += f"\n[LOOP_BREAK] phase_advanced_to={gate.phase.value}"
                else:
                    await _emit_phase_gate_blocked(
                        state,
                        requested_phase=nxt.value,
                        reason=f"loop break fallback blocked: {gate.reason}",
                        missing=gate.missing or ["phase_transition_guard"],
                    )
            else:
                state.set_phase(Phase.DONE)
                state.status = TaskStatus.DONE
                await _emit(
                    TraceEvent(
                        task_id=state.task_id,
                        timestamp=_ts(),
                        event_type="TASK_COMPLETED",
                        source_module="orchestrator",
                        payload={"final_status": state.status.value, "reason": "loop break finish"},
                    )
                )
    state.updated_at = datetime.utcnow()

    return effective_status_by_skill


async def _recon_followup_katana_from_dirsearch(
    state: TaskState,
    shared_dir: str,
    available_skills: list[str],
    enable_executor: bool,
    iml: int,
    skill_statuses: dict[str, str],
    *,
    current_todo: Any = None,
    task_store: Optional[TaskStore] = None,
) -> None:
    """
    dirsearch 写入 shared discovery/dirsearch.json 后，抽取 URL 再跑一轮 Katana（仅 extra seeds，合并进 katana_urls.txt）。
    """
    seeds = katana_seeds_from_dirsearch_json(Path(shared_dir) / "dirsearch.json", max_seeds=24)
    if not seeds or "katana" not in available_skills:
        if state.target_context is not None:
            state.target_context["_recon_katana_followup_ran"] = False
            state.target_context["_recon_katana_followup_seed_count"] = 0
        return
    follow = [
        ActionItem(
            skill_id="katana",
            params={
                "output_dir": shared_dir,
                "skip_dirsearch": True,
                "katana_field_scope": "fqdn",
                "katana_strict_crawl_scope": True,
                "katana_extra_seeds": seeds,
                "katana_skip_initial_target": True,
                "timeout": _skill_timeout_cap(),
            },
        )
    ]
    exec_follow, pre_skips_fu = preflight_actions(state, follow, iml=iml)
    if pre_skips_fu:
        pl: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "information_maturity_level": iml,
            "skipped_actions": pre_skips_fu,
            "reason": "recon_katana_followup_preflight",
        }
        if current_todo:
            pl["todo_id"] = current_todo.id
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PREFLIGHT_CHECK",
                source_module="orchestrator",
                payload=pl,
            )
        )
    if not exec_follow:
        if state.target_context is not None:
            state.target_context["_recon_katana_followup_ran"] = False
            state.target_context["_recon_katana_followup_seed_count"] = len(seeds)
        return
    for a in exec_follow:
        if (a.skill_id or "").lower().strip() == "katana":
            a.params.setdefault("output_dir", shared_dir)
            a.params.setdefault("skip_dirsearch", True)
    fu_payload: Dict[str, Any] = {
        "phase": state.current_phase.value,
        "skill_id": "katana",
        "seed_count": len(seeds),
        "seeds_preview": seeds[:12],
    }
    if current_todo:
        fu_payload["todo_id"] = current_todo.id
    await _emit(
        TraceEvent(
            task_id=state.task_id,
            timestamp=_ts(),
            event_type="RECON_DETERMINISTIC_FOLLOWUP",
            source_module="orchestrator",
            payload=fu_payload,
        )
    )
    fu = await _run_actions_and_merge_results(
        state,
        exec_follow,
        available_skills,
        enable_executor,
        current_todo=current_todo,
        task_store=task_store,
    )
    st = (fu.get("katana") or "").strip().upper()
    if st and st not in ("SUCCESS", "OK"):
        skill_statuses["katana"] = st
    if state.target_context is not None:
        state.target_context["_recon_katana_followup_ran"] = True
        state.target_context["_recon_katana_followup_seed_count"] = len(seeds)
        state.target_context["_recon_katana_followup_status"] = st or skill_statuses.get("katana", "")


async def tick(state: TaskState, *, enable_executor: bool) -> None:
    """
    执行一次编排步进（tick）：
    若任务已终态则直接返回；否则根据当前阶段做决策并执行，更新 state（原地修改）。
    决策/执行失败会抛出 HTTPException 或 httpx.HTTPError，由上层转换为 502。
    """
    if state.is_terminal():
        return

    # 第一次 tick：PENDING -> RUNNING + RECON
    if state.status == TaskStatus.PENDING:
        state.status = TaskStatus.RUNNING
        state.set_phase(Phase.RECON)
        state.updated_at = datetime.utcnow()
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PHASE_START",
                source_module="orchestrator",
                payload={"phase": state.current_phase.value},
            )
        )
        return

    await _maybe_enforce_phase_wall_clock_budget(state)
    await _maybe_enforce_phase_cost_budget(state)
    await _maybe_enforce_phase_plan_round_cap(state)

    # REPORT 阶段：同步上下文后触发报告事件并结算为 DONE
    if state.current_phase == Phase.REPORT:
        await put_context(state.task_id, state.target_context)
        try:
            write_task_context(state.task_id, state.target_context)
        except Exception:
            pass
        coverage_overview = getattr(state, "coverage_attempted", None) or []
        coverage_gaps = await compute_report_coverage_gaps(coverage_overview)
        if await _enforce_report_finish_gate(state, coverage_gaps):
            await put_context(state.task_id, state.target_context)
            try:
                write_task_context(state.task_id, state.target_context)
            except Exception:
                pass
            return
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="REPORT_REQUESTED",
                source_module="orchestrator",
                payload={
                    "phase": "REPORT",
                    "message": "Report generation placeholder; hook report service here.",
                    "context_keys": list(state.target_context.keys()) if state.target_context else [],
                    "coverage_overview": coverage_overview,
                    "coverage_gaps": coverage_gaps,
                },
            )
        )
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PHASE_END",
                source_module="orchestrator",
                payload={"phase": state.current_phase.value},
            )
        )
        state.set_phase(Phase.DONE)
        state.status = TaskStatus.DONE
        state.updated_at = datetime.utcnow()
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="TASK_COMPLETED",
                source_module="orchestrator",
                payload={"final_status": state.status.value},
            )
        )
        return

    # RUNNING 且非 REPORT：先注入聚类/路径轮廓，再跑 THREAT_MODEL 框架硬规则（硬规则可消费 asset_path_profile）
    _inject_clustered_targets_preview(state)
    await _apply_threat_model_framework_hard_rule(state)
    # Fix D6：若 framework_target 还停留在 GENERIC_WEB / CUSTOM_APP marker，但 VULN_SCAN 后
    # 新累积的 tech_stack_evidence 已能识别具体框架，则升级。
    # 实地证据 (round 8 thinkphp): D5 在 RECON→VULN_SCAN 转场时运行得太早，evidence 还空；
    # nuclei 在 VULN_SCAN 填充 tech_stack_evidence 后，framework_target 却不再回评估。
    await _maybe_upgrade_framework_target(state)

    # 从执行器拉取当前阶段可用 skill 列表，再调用 LLM 决策
    available_skills = await fetch_skills_for_phase(state.current_phase.value)
    _inject_memory_views_into_context(state)
    iml, _iml_detail = ensure_iml_in_context(state)
    available_skills = filter_available_skills_by_iml(
        available_skills, iml, state.current_phase.value
    )
    available_skills = bias_available_skills_for_decision(
        state.current_phase, available_skills, state.target_context or {}
    )
    available_skills = ensure_baidu_search_offered(state.current_phase.value, available_skills)
    await _inject_threat_model_framework_requirements(
        state,
        available_skills,
    )

    # 路线 C：RECON 确定性微流
    # - 首轮并发 [katana, dirsearch]；dirsearch 落盘后解析 dirsearch.json，有种子则再跑一轮 Katana（合并 katana_urls.txt）
    # - 全部成功后标记 _recon_deterministic_done，才允许 LLM 决策
    if state.current_phase == Phase.RECON and not _recon_deterministic_enabled():
        if not bool((state.target_context or {}).get("_recon_deterministic_disabled_emitted")):
            state.target_context["_recon_deterministic_disabled_emitted"] = True
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="RECON_DETERMINISTIC_DISABLED",
                    source_module="orchestrator",
                    payload={
                        "phase": state.current_phase.value,
                        "ORCH_RECON_DETERMINISTIC_ENABLED": False,
                        "note": "skip deterministic warmup and allow LLM decision from first round",
                    },
                )
            )
    if state.current_phase == Phase.RECON and _recon_deterministic_enabled():
        recon_done = bool((state.target_context or {}).get("_recon_deterministic_done"))
        allow_llm = bool((state.target_context or {}).get("_recon_deterministic_allow_llm"))
        if not recon_done and not allow_llm:
            run_id_ctx = str((state.target_context or {}).get("run_id") or "").strip()
            if not run_id_ctx:
                seed = f"{state.task_id}|{state.target}|recon"
                run_id_ctx = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
                state.target_context["run_id"] = run_id_ctx
            shared_dir = _shared_target_discovery_dir(state.task_id, run_id_ctx)
            fixed_actions: list[ActionItem] = []
            if "katana" in available_skills:
                fixed_actions.append(
                    ActionItem(
                        skill_id="katana",
                        params={
                            "output_dir": shared_dir,
                            # 外部 dirsearch 为准；避免 katana 内置 dirsearch 产生覆盖/竞争
                            "skip_dirsearch": True,
                            "katana_field_scope": "fqdn",
                            "katana_strict_crawl_scope": True,
                            "timeout": _skill_timeout_cap(),
                        },
                    )
                )
            if "dirsearch" in available_skills:
                fixed_actions.append(
                    ActionItem(
                        skill_id="dirsearch",
                        params={
                            "output_dir": shared_dir,
                            "timeout": _skill_timeout_cap(),
                        },
                    )
                )

            if fixed_actions:
                # 先走统一 preflight（策略参数/种子注入/IML 约束）
                exec_actions, pre_skips = preflight_actions(state, fixed_actions, iml=iml)
                if pre_skips:
                    await _emit(
                        TraceEvent(
                            task_id=state.task_id,
                            timestamp=_ts(),
                            event_type="PREFLIGHT_CHECK",
                            source_module="orchestrator",
                            payload={
                                "phase": state.current_phase.value,
                                "information_maturity_level": iml,
                                "skipped_actions": pre_skips,
                            },
                        )
                    )

                # 再确保共享输出目录参数没有被预检流程覆盖
                for a in exec_actions:
                    if (a.skill_id or "").lower().strip() == "katana":
                        a.params.setdefault("output_dir", shared_dir)
                        a.params.setdefault("skip_dirsearch", True)
                    elif (a.skill_id or "").lower().strip() == "dirsearch":
                        a.params.setdefault("output_dir", shared_dir)

                await _emit(
                    TraceEvent(
                        task_id=state.task_id,
                        timestamp=_ts(),
                        event_type="RECON_DETERMINISTIC_START",
                        source_module="orchestrator",
                        payload={
                            "phase": state.current_phase.value,
                            "shared_discovery_dir": shared_dir,
                            "fixed_actions": [a.model_dump() for a in exec_actions],
                        },
                    )
                )

                skill_statuses = await _run_actions_and_merge_results(
                    state,
                    exec_actions,
                    available_skills,
                    enable_executor,
                )

                def _is_success(v: str) -> bool:
                    return (v or "").strip().upper() in ("SUCCESS", "OK")

                katana_ok = _is_success(skill_statuses.get("katana", ""))
                dirsearch_ok = _is_success(skill_statuses.get("dirsearch", ""))
                if katana_ok and dirsearch_ok:
                    await _recon_followup_katana_from_dirsearch(
                        state,
                        shared_dir,
                        available_skills,
                        enable_executor,
                        iml,
                        skill_statuses,
                    )
                    katana_ok = _is_success(skill_statuses.get("katana", ""))

                attempts = int((state.target_context or {}).get("_recon_deterministic_attempts") or 0) + 1
                state.target_context["_recon_deterministic_attempts"] = attempts

                if katana_ok and dirsearch_ok:
                    state.target_context["_recon_deterministic_done"] = True
                    await _emit(
                        TraceEvent(
                            task_id=state.task_id,
                            timestamp=_ts(),
                            event_type="RECON_DETERMINISTIC_DONE",
                            source_module="orchestrator",
                            payload={
                                "katana_ok": katana_ok,
                                "dirsearch_ok": dirsearch_ok,
                                "katana_followup_ran": bool(
                                    (state.target_context or {}).get("_recon_katana_followup_ran")
                                ),
                                "katana_followup_seed_count": int(
                                    (state.target_context or {}).get("_recon_katana_followup_seed_count") or 0
                                ),
                            },
                        )
                    )
                else:
                    # 防止永远卡在同一套确定性动作：失败两次后允许 LLM 换假设
                    if attempts >= 2:
                        state.target_context["_recon_deterministic_allow_llm"] = True
                    await _emit(
                        TraceEvent(
                            task_id=state.task_id,
                            timestamp=_ts(),
                            event_type="RECON_DETERMINISTIC_NOT_READY",
                            source_module="orchestrator",
                            payload={
                                "katana_ok": katana_ok,
                                "dirsearch_ok": dirsearch_ok,
                                "attempts": attempts,
                                "allow_llm_after_attempts_gte_2": True,
                            },
                        )
                    )
                    state.updated_at = datetime.utcnow()
                    if not state.target_context.get("_recon_deterministic_allow_llm"):
                        return

            # 若 katana/dirsearch 任一都不在 available_skills 中，则退回 LLM

    skills_for_plan_llm = list(available_skills)
    if await try_dispatch_pending_plan_list_item(
        state=state,
        available_skills=available_skills,
        enable_executor=enable_executor,
        _emit=_emit,
        _ts=_ts,
        _run_actions_and_merge_results=_run_actions_and_merge_results,
        current_todo=None,
        task_store=None,
    ):
        return
    try:
        plan_list, orch_llm_usage = await call_plan_list_decision_engine(
            task_id=state.task_id,
            phase=state.current_phase,
            target_context=state.target_context,
            history_summary=state.history_summary,
            available_skill_ids=skills_for_plan_llm,
            summary_chunks=getattr(state, "recent_summary_chunks", None) or [],
        )
    except LLMCallFailed as exc:
        await _accumulate_finops_from_orch_llm(state, exc.accrued_llm_usage)
        raise
    await _accumulate_finops_from_orch_llm(state, orch_llm_usage)
    plan_list = merge_kb_hits_into_plan_chunk_refs(
        plan_list,
        state.target_context.get("kb_hits"),
        available_skill_ids=available_skills,
        skill_aliases=_SKILL_ID_ALIASES,
    )
    crm_raw = state.target_context.setdefault("_plan_chunk_ref_counts", {})
    if not isinstance(crm_raw, dict):
        crm_raw = {}
        state.target_context["_plan_chunk_ref_counts"] = crm_raw
    ok_biz, biz_err = validate_plan_list_business(
        plan_list,
        expected_task_id=state.task_id,
        available_skill_ids=available_skills,
        task_target=state.target,
        skill_aliases=_SKILL_ID_ALIASES,
        current_phase=state.current_phase,
        known_chunk_ids=_known_chunk_ids_from_context(state.target_context, task_id=state.task_id),
        chunk_ref_counts=crm_raw,
    )
    if not ok_biz and biz_err:
        state.target_context.pop(LATEST_PLAN_LIST_CONTEXT_KEY, None)
        stash_plan_list_validation_error(state.target_context, biz_err)
        try:
            write_task_context(state.task_id, state.target_context)
        except Exception:
            pass
        biz_payload: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "orch_plan_mode_enabled": True,
            "structured_error": plan_error_envelope_to_client_dict(biz_err),
        }
        attach_correlation(biz_payload, state.task_id)
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PLAN_LIST_BUSINESS_REJECT",
                source_module="orchestrator",
                payload=biz_payload,
            )
        )
        vio = _brief_plan_list_violations_for_history(biz_err)
        fix = "Fix: set context_chunk_refs=[] for any item with invalid/unknown chunk_id and replan."
        state.history_summary = (
            (state.history_summary or "").strip()
            + "\n[PLAN_LIST_REJECTED] "
            + (biz_err.message or "business validation failed")
            + (f"; violations: {vio}." if vio else "")
            + f" {fix}"
        ).strip()
        state.updated_at = datetime.utcnow()
        return
    plan_list = materialize_kit_anchor_plan_list(plan_list, available_skills)
    seen_chunk_ids: set[str] = set()
    for pit in plan_list.items or []:
        for ref in pit.context_chunk_refs or []:
            cid = str(getattr(ref, "chunk_id", "") or "").strip()
            if not cid or cid in seen_chunk_ids:
                continue
            seen_chunk_ids.add(cid)
            crm_raw[cid] = int(crm_raw.get(cid) or 0) + 1
    plan_list = sanitize_plan_list_metadata(plan_list)
    await _handle_plan_list_decision_path(state, plan_list, todo_id=None)
    return


async def tick_manager(
    state: TaskState,
    *,
    enable_executor: bool,
    task_store: TaskStore | None = None,
) -> None:
    """
    Manager Agent 路径：与 tick() 相同的单次步进语义，但在决策前维护 TodoList，
    将当前选中的 Todo 注入上下文，执行时携带 todo_id，执行后更新 Todo 状态并写回 _todos。
    """
    if state.is_terminal():
        return

    # 第一次 tick：PENDING -> RUNNING + RECON（与 tick 一致）
    if state.status == TaskStatus.PENDING:
        state.status = TaskStatus.RUNNING
        state.set_phase(Phase.RECON)
        state.updated_at = datetime.utcnow()
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PHASE_START",
                source_module="orchestrator",
                payload={"phase": state.current_phase.value},
            )
        )
        return

    await _maybe_enforce_phase_wall_clock_budget(state)
    await _maybe_enforce_phase_cost_budget(state)
    await _maybe_enforce_phase_plan_round_cap(state)

    # REPORT 阶段：与 tick 一致
    if state.current_phase == Phase.REPORT:
        await put_context(state.task_id, state.target_context)
        try:
            write_task_context(state.task_id, state.target_context)
        except Exception:
            pass
        coverage_overview = getattr(state, "coverage_attempted", None) or []
        coverage_gaps = await compute_report_coverage_gaps(coverage_overview)
        if await _enforce_report_finish_gate(state, coverage_gaps):
            await put_context(state.task_id, state.target_context)
            try:
                write_task_context(state.task_id, state.target_context)
            except Exception:
                pass
            return
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="REPORT_REQUESTED",
                source_module="orchestrator",
                payload={
                    "phase": "REPORT",
                    "message": "Report generation placeholder; hook report service here.",
                    "context_keys": list(state.target_context.keys()) if state.target_context else [],
                    "coverage_overview": coverage_overview,
                    "coverage_gaps": coverage_gaps,
                },
            )
        )
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PHASE_END",
                source_module="orchestrator",
                payload={"phase": state.current_phase.value},
            )
        )
        state.set_phase(Phase.DONE)
        state.status = TaskStatus.DONE
        state.updated_at = datetime.utcnow()
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="TASK_COMPLETED",
                source_module="orchestrator",
                payload={"final_status": state.status.value},
            )
        )
        return

    # RUNNING 且非 REPORT：维护 TodoList，注入当前 Todo，再决策
    # v1：Todo 的权威来源为 TaskStore；_todos 仅作为本次 tick 的临时视图，结束时会从 context 中移除。
    if task_store is not None:
        # 1) 先从 TaskStore 加载已有 Todo，填充到 state.target_context["_todos"] 中
        store_items = await load_todos_from_store(task_store, state.task_id)
        if store_items:
            persist_todos_to_state(state, store_items)

    # 2) 基于当前上下文规则补充 Todo
    ensure_todos_from_context(state)
    # 3) 将合并后的 Todo 列表回写到 TaskStore，作为新的权威视图
    if task_store is not None:
        todos_after_rule = get_todos_from_state(state)
        await persist_todos_to_store(task_store, state.task_id, todos_after_rule)

    current_todo = pick_next_pending_todo(state)
    inject_current_todo_into_context(state, current_todo)
    try:
        _inject_clustered_targets_preview(state)
        await _apply_threat_model_framework_hard_rule(
            state,
            todo_id=current_todo.id if current_todo else None,
        )
        # Fix D6: same marker → specific-framework upgrade as plain tick()
        await _maybe_upgrade_framework_target(state)
        available_skills = await fetch_skills_for_phase(state.current_phase.value)
        _inject_memory_views_into_context(state)
        iml_mgr, _iml_detail_mgr = ensure_iml_in_context(state)
        available_skills = filter_available_skills_by_iml(
            available_skills, iml_mgr, state.current_phase.value
        )
        available_skills = bias_available_skills_for_decision(
            state.current_phase, available_skills, state.target_context or {}
        )
        available_skills = ensure_baidu_search_offered(state.current_phase.value, available_skills)
        await _inject_threat_model_framework_requirements(
            state,
            available_skills,
            todo_id=current_todo.id if current_todo else None,
        )

        # 路线 C：RECON 确定性微流（首轮 katana+dirsearch，再按 dirsearch.json 补跑 Katana）
        if state.current_phase == Phase.RECON and not _recon_deterministic_enabled():
            if not bool((state.target_context or {}).get("_recon_deterministic_disabled_emitted")):
                state.target_context["_recon_deterministic_disabled_emitted"] = True
                await _emit(
                    TraceEvent(
                        task_id=state.task_id,
                        timestamp=_ts(),
                        event_type="RECON_DETERMINISTIC_DISABLED",
                        source_module="orchestrator",
                        payload={
                            "phase": state.current_phase.value,
                            "ORCH_RECON_DETERMINISTIC_ENABLED": False,
                            "note": "skip deterministic warmup and allow LLM decision from first round",
                            "todo_id": current_todo.id if current_todo else None,
                        },
                    )
                )
        if state.current_phase == Phase.RECON and _recon_deterministic_enabled():
            recon_done = bool((state.target_context or {}).get("_recon_deterministic_done"))
            allow_llm = bool((state.target_context or {}).get("_recon_deterministic_allow_llm"))
            if not recon_done and not allow_llm:
                run_id_ctx = str((state.target_context or {}).get("run_id") or "").strip()
                if not run_id_ctx:
                    seed = f"{state.task_id}|{state.target}|recon-manager"
                    run_id_ctx = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
                    state.target_context["run_id"] = run_id_ctx
                shared_dir = _shared_target_discovery_dir(state.task_id, run_id_ctx)
                fixed_actions: list[ActionItem] = []
                if "katana" in available_skills:
                    fixed_actions.append(
                        ActionItem(
                            skill_id="katana",
                            params={
                                "output_dir": shared_dir,
                                "skip_dirsearch": True,
                                "katana_field_scope": "fqdn",
                                "katana_strict_crawl_scope": True,
                                "timeout": _skill_timeout_cap(),
                            },
                        )
                    )
                if "dirsearch" in available_skills:
                    fixed_actions.append(
                        ActionItem(
                            skill_id="dirsearch",
                            params={"output_dir": shared_dir, "timeout": _skill_timeout_cap()},
                        )
                    )

                if fixed_actions:
                    exec_actions, pre_skips_mgr = preflight_actions(state, fixed_actions, iml=iml_mgr)
                    if pre_skips_mgr:
                        await _emit(
                            TraceEvent(
                                task_id=state.task_id,
                                timestamp=_ts(),
                                event_type="PREFLIGHT_CHECK",
                                source_module="orchestrator",
                                payload={
                                    "phase": state.current_phase.value,
                                    "information_maturity_level": iml_mgr,
                                    "skipped_actions": pre_skips_mgr,
                                    "todo_id": current_todo.id if current_todo else None,
                                },
                            )
                        )

                    for a in exec_actions:
                        if (a.skill_id or "").lower().strip() == "katana":
                            a.params.setdefault("output_dir", shared_dir)
                            a.params.setdefault("skip_dirsearch", True)
                        elif (a.skill_id or "").lower().strip() == "dirsearch":
                            a.params.setdefault("output_dir", shared_dir)

                    await _emit(
                        TraceEvent(
                            task_id=state.task_id,
                            timestamp=_ts(),
                            event_type="RECON_DETERMINISTIC_START",
                            source_module="orchestrator",
                            payload={
                                "phase": state.current_phase.value,
                                "shared_discovery_dir": shared_dir,
                                "todo_id": current_todo.id if current_todo else None,
                                "fixed_actions": [a.model_dump() for a in exec_actions],
                            },
                        )
                    )

                    skill_statuses = await _run_actions_and_merge_results(
                        state,
                        exec_actions,
                        available_skills,
                        enable_executor,
                        current_todo=current_todo,
                        task_store=task_store,
                    )

                    def _is_success(v: str) -> bool:
                        return (v or "").strip().upper() in ("SUCCESS", "OK")

                    katana_ok = _is_success(skill_statuses.get("katana", ""))
                    dirsearch_ok = _is_success(skill_statuses.get("dirsearch", ""))
                    if katana_ok and dirsearch_ok:
                        await _recon_followup_katana_from_dirsearch(
                            state,
                            shared_dir,
                            available_skills,
                            enable_executor,
                            iml_mgr,
                            skill_statuses,
                            current_todo=current_todo,
                            task_store=task_store,
                        )
                        katana_ok = _is_success(skill_statuses.get("katana", ""))

                    attempts = int((state.target_context or {}).get("_recon_deterministic_attempts") or 0) + 1
                    state.target_context["_recon_deterministic_attempts"] = attempts

                    if katana_ok and dirsearch_ok:
                        state.target_context["_recon_deterministic_done"] = True
                        await _emit(
                            TraceEvent(
                                task_id=state.task_id,
                                timestamp=_ts(),
                                event_type="RECON_DETERMINISTIC_DONE",
                                source_module="orchestrator",
                                payload={
                                    "katana_ok": katana_ok,
                                    "dirsearch_ok": dirsearch_ok,
                                    "katana_followup_ran": bool(
                                        (state.target_context or {}).get("_recon_katana_followup_ran")
                                    ),
                                    "katana_followup_seed_count": int(
                                        (state.target_context or {}).get("_recon_katana_followup_seed_count") or 0
                                    ),
                                    "todo_id": current_todo.id if current_todo else None,
                                },
                            )
                        )
                    else:
                        if attempts >= 2:
                            state.target_context["_recon_deterministic_allow_llm"] = True
                        await _emit(
                            TraceEvent(
                                task_id=state.task_id,
                                timestamp=_ts(),
                                event_type="RECON_DETERMINISTIC_NOT_READY",
                                source_module="orchestrator",
                                payload={
                                    "katana_ok": katana_ok,
                                    "dirsearch_ok": dirsearch_ok,
                                    "attempts": attempts,
                                    "allow_llm_after_attempts_gte_2": True,
                                    "todo_id": current_todo.id if current_todo else None,
                                },
                            )
                        )
                        if not state.target_context.get("_recon_deterministic_allow_llm"):
                            return

                # 若 fixed_actions 为空，则退回 LLM

        skills_for_plan_llm = list(available_skills)
        if await try_dispatch_pending_plan_list_item(
            state=state,
            available_skills=available_skills,
            enable_executor=enable_executor,
            _emit=_emit,
            _ts=_ts,
            _run_actions_and_merge_results=_run_actions_and_merge_results,
            current_todo=current_todo,
            task_store=task_store,
        ):
            return
        try:
            plan_list, orch_llm_usage_mgr = await call_plan_list_decision_engine(
                task_id=state.task_id,
                phase=state.current_phase,
                target_context=state.target_context,
                history_summary=state.history_summary,
                available_skill_ids=skills_for_plan_llm,
                summary_chunks=getattr(state, "recent_summary_chunks", None) or [],
            )
        except LLMCallFailed as exc:
            await _accumulate_finops_from_orch_llm(state, exc.accrued_llm_usage)
            raise
        await _accumulate_finops_from_orch_llm(state, orch_llm_usage_mgr)
        plan_list = merge_kb_hits_into_plan_chunk_refs(
            plan_list,
            state.target_context.get("kb_hits"),
            available_skill_ids=available_skills,
            skill_aliases=_SKILL_ID_ALIASES,
        )
        crm_mgr = state.target_context.setdefault("_plan_chunk_ref_counts", {})
        if not isinstance(crm_mgr, dict):
            crm_mgr = {}
            state.target_context["_plan_chunk_ref_counts"] = crm_mgr
        ok_biz, biz_err = validate_plan_list_business(
            plan_list,
            expected_task_id=state.task_id,
            available_skill_ids=available_skills,
            task_target=state.target,
            skill_aliases=_SKILL_ID_ALIASES,
            current_phase=state.current_phase,
            known_chunk_ids=_known_chunk_ids_from_context(state.target_context, task_id=state.task_id),
            chunk_ref_counts=crm_mgr,
        )
        if not ok_biz and biz_err:
            state.target_context.pop(LATEST_PLAN_LIST_CONTEXT_KEY, None)
            stash_plan_list_validation_error(state.target_context, biz_err)
            try:
                write_task_context(state.task_id, state.target_context)
            except Exception:
                pass
            biz_payload_mgr: Dict[str, Any] = {
                "phase": state.current_phase.value,
                "orch_plan_mode_enabled": True,
                "structured_error": plan_error_envelope_to_client_dict(biz_err),
                "todo_id": current_todo.id if current_todo else None,
            }
            attach_correlation(biz_payload_mgr, state.task_id)
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="PLAN_LIST_BUSINESS_REJECT",
                    source_module="orchestrator",
                    payload=biz_payload_mgr,
                )
            )
            vio_mgr = _brief_plan_list_violations_for_history(biz_err)
            fix_mgr = "Fix: set context_chunk_refs=[] for any item with invalid/unknown chunk_id and replan."
            state.history_summary = (
                (state.history_summary or "").strip()
                + "\n[PLAN_LIST_REJECTED] "
                + (biz_err.message or "business validation failed")
                + (f"; violations: {vio_mgr}." if vio_mgr else "")
                + f" {fix_mgr}"
            ).strip()
            state.updated_at = datetime.utcnow()
            return
        plan_list = materialize_kit_anchor_plan_list(plan_list, available_skills)
        seen_chunk_mgr: set[str] = set()
        for pit in plan_list.items or []:
            for ref in pit.context_chunk_refs or []:
                cid = str(getattr(ref, "chunk_id", "") or "").strip()
                if not cid or cid in seen_chunk_mgr:
                    continue
                seen_chunk_mgr.add(cid)
                crm_mgr[cid] = int(crm_mgr.get(cid) or 0) + 1
        plan_list = sanitize_plan_list_metadata(plan_list)
        await _handle_plan_list_decision_path(
            state,
            plan_list,
            todo_id=current_todo.id if current_todo else None,
        )
        return
    finally:
        clear_current_todo_from_context(state)
        # v1：Todo 列表的权威来源为 TaskStore，target_context 中不再长期保存完整 _todos，仅保留当前 Todo / 统计类信息。
        if state.target_context:
            state.target_context.pop("_todos", None)
