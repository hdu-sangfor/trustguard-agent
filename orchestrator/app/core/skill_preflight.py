"""
编排器 Pre-flight：在执行 SKILL 前基于信息完备性 (IML) 做解锁与参数整形，
避免 katana 空跑后仍盲目调用 dispatcher/nuclei/dirsearch。

具体工具的参数改写由 policy_engine.PolicyEngine 按 skill_id 分发（见 preflight_nuclei / preflight_dispatcher）。

可通过环境变量关闭：
  ORCHESTRATOR_IML_PREFLIGHT=0  — 关闭预检与参数注入
  ORCHESTRATOR_IML_FILTER_SKILLS=0 — 仅预检/注入，不在决策前过滤可用技能列表
  ORCHESTRATOR_NUCLEI_SNIPER_PREFLIGHT=0 — 关闭 nuclei 标签狙击重写（仍保留 IML 标签约束）
  ORCHESTRATOR_NUCLEI_SNIPER_RATE_LIMIT=30 — 狙击模式下的 nuclei -rl
  ORCHESTRATOR_NUCLEI_SNIPER_TIMEOUT_CAP= — 若设置正整数，则狙击时将 timeout/nuclei_timeout 上限钳制到该值
  ORCHESTRATOR_SKILL_POLICIES_YAML= — 覆盖默认策略文件路径
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.core.context_pipeline import normalize_pipeline_context
from app.core.http_enum_seeds import extract_http_enum_seed_urls, normalize_url_for_pipeline
from app.core.information_maturity import compute_information_maturity
from app.core.policy_engine import PolicyEngine
from app.core.preflight_context import PreflightContext
from app.core.preflight_nuclei import clamp_nuclei_tags_for_iml
from app.models import ActionItem, TaskState

logger = logging.getLogger(__name__)

_PREFLIGHT_ON = (os.getenv("ORCHESTRATOR_IML_PREFLIGHT", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
_FILTER_SKILLS_ON = (os.getenv("ORCHESTRATOR_IML_FILTER_SKILLS", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_PIPELINE_SKILLS = frozenset({"katana", "dispatcher", "nuclei", "dirsearch"})
_WEB_VULN_MACRO = frozenset({"web-vuln-pipeline"})

_IML_INDEPENDENT_SKILL_IDS = frozenset(
    {
        "nmap",
        "whatweb-fingerprint",
        "whatweb",
        "web-fingerprint",
    }
)


def _is_iml_independent_skill(skill_id: str) -> bool:
    sl = (skill_id or "").strip().lower()
    return sl in _IML_INDEPENDENT_SKILL_IDS


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def _collect_seed_urls(ctx: dict[str, Any]) -> list[str]:
    seeds = list(extract_http_enum_seed_urls(ctx))

    # Katana / dispatcher 注入的回退种子
    for key in ("katana_discovery_fallback_seed_urls", "discovery_fallback_seed_urls"):
        v = ctx.get(key)
        if isinstance(v, list):
            for x in v:
                s = str(x).strip()
                if s:
                    seeds.append(s)

    # RECON 阶段从 clustered_targets.txt 提取的代表路径，作为高价值种子参与后续流水线
    v = ctx.get("clustered_targets_preview")
    if isinstance(v, list):
        for x in v:
            s = str(x).strip()
            if s:
                seeds.append(s)
    seen: set[str] = set()
    out: list[str] = []
    for u in seeds:
        clean = normalize_url_for_pipeline(u)
        if clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _katana_total_raw(ctx: dict[str, Any]) -> int | None:
    u = ctx.get("katana_url_counts") or ctx.get("url_counts")
    if isinstance(u, dict):
        try:
            return int(u.get("total_raw") or 0)
        except Exception:
            return 0
    return None


def _pipeline_input_starved(ctx: dict[str, Any]) -> bool:
    seeds = _collect_seed_urls(ctx)
    if seeds:
        return False
    tr = _katana_total_raw(ctx)
    if tr is None:
        return False
    return tr == 0


def filter_available_skills_by_iml(available_skills: list[str], iml: int, phase_value: str) -> list[str]:
    if not _FILTER_SKILLS_ON or not available_skills:
        return list(available_skills)
    ph = (phase_value or "").upper()
    if ph != "VULN_SCAN":
        return list(available_skills)
    out: list[str] = []
    for sid in available_skills:
        if _is_iml_independent_skill(sid):
            out.append(sid)
            continue
        sl = sid.lower()
        if sl in ("nuclei", "dispatcher") and iml < 1:
            continue
        if sl == "dirsearch" and iml < 2:
            continue
        if sl == "nuclei" and iml < 1:
            continue
        out.append(sid)
    return out if out else list(available_skills)


def ensure_iml_in_context(state: TaskState) -> tuple[int, dict[str, Any]]:
    ws = (os.getenv("ORCHESTRATOR_WORKSPACE_ROOT") or os.getenv("WORKSPACE_ROOT") or "").strip() or None
    iml, detail = compute_information_maturity(
        state.target_context or {},
        state.target or "",
        task_id=state.task_id,
        workspace_root=ws,
    )
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    ctx["information_maturity_level"] = iml
    ctx["information_maturity_detail"] = detail
    normalize_pipeline_context(ctx)
    state.target_context = ctx
    return iml, detail


def preflight_actions(
    state: TaskState, actions: list[ActionItem], *, iml: int
) -> tuple[list[ActionItem], list[dict[str, Any]]]:
    if not _PREFLIGHT_ON or not actions:
        return actions, []
    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    seeds = _collect_seed_urls(ctx)
    starved = _pipeline_input_starved(ctx)
    run_id_key = "web_vuln_run_id"

    out: list[ActionItem] = []
    skipped: list[dict[str, Any]] = []
    for item in actions:
        sid = (item.skill_id or "").strip().lower()
        params = dict(item.params or {})

        if sid in _PIPELINE_SKILLS or sid in _WEB_VULN_MACRO:
            if starved and sid in ("dispatcher", "nuclei") and not _truthy_env(
                "ORCHESTRATOR_ALLOW_STARVED_PIPELINE", "0"
            ):
                logger.info(
                    "preflight skip skill=%s task=%s (pipeline_input_starved, seeds=%s)",
                    sid,
                    state.task_id,
                    len(seeds),
                )
                skipped.append({"skill_id": sid, "reason": "pipeline_input_starved"})
                continue

        pctx = PreflightContext(
            state=state,
            ctx=ctx,
            iml=iml,
            seeds=seeds,
            run_id_key=run_id_key,
        )
        PolicyEngine.apply(sid, params, pctx)

        item.params = params
        out.append(item)

    state.target_context = ctx
    return out, skipped


__all__ = [
    "clamp_nuclei_tags_for_iml",
    "ensure_iml_in_context",
    "filter_available_skills_by_iml",
    "preflight_actions",
]
