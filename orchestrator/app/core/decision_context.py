"""分层决策上下文组装器（Tiered Context Assembler）。"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.memory_store import redact_canonical_params_for_llm

try:
    import tiktoken  # type: ignore
except Exception:
    tiktoken = None

_TOTAL_TOKEN_BUDGET = int(os.getenv("DECISION_CONTEXT_TOTAL_TOKENS", "6000"))
_TIER01_BUDGET = int(os.getenv("DECISION_CONTEXT_TIER01_TOKENS", "2000"))
_TIER2_BUDGET = int(os.getenv("DECISION_CONTEXT_TIER2_TOKENS", "2000"))
_STRICT_TIER01_ASSERT = os.getenv("DECISION_CONTEXT_STRICT_TIER01_ASSERT", "true").strip().lower() == "true"
_MAX_SUMMARY_CHUNKS = int(os.getenv("DECISION_CONTEXT_MAX_SUMMARY_CHUNKS", "10"))
_MAX_STR_LEN = int(os.getenv("DECISION_CONTEXT_MAX_STRING_CHARS", "1800"))
_RAW_PREVIEW_MAX_LEN = int(os.getenv("DECISION_CONTEXT_RAW_PREVIEW_MAX_CHARS", "1500"))


def _count_tokens(s: str) -> int:
    text = s or ""
    if tiktoken is None:
        return max(1, len(text) // 4)
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _clip_str(s: str, max_len: int = _MAX_STR_LEN) -> str:
    return s if len(s) <= max_len else s[:max_len] + "...[truncated]"


def _json_dump(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


def _summarize_plan_list_validation_error_brief(ctx: dict[str, Any]) -> dict[str, Any] | None:
    raw = ctx.get("_latest_plan_list_validation_error")
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "").strip()
    msg = str(raw.get("message") or "").strip()
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    violations = details.get("violations") if isinstance(details, dict) else None
    top: list[dict[str, Any]] = []
    if isinstance(violations, list):
        for v in violations[:5]:
            if not isinstance(v, dict):
                continue
            row: dict[str, Any] = {}
            for k in ("plan_id", "code", "chunk_id", "skill_id", "message", "resolved_skill_id"):
                if k in v and v.get(k) is not None:
                    row[k] = _clip_str(str(v.get(k)), 220)
            if row:
                top.append(row)
    remediation = (
        "If any violation indicates invalid/unknown chunk_id, set that item's context_chunk_refs=[] and replan. "
        "Only reference real chunk_ids copied verbatim from retrieval context."
    )
    out = {
        "code": _clip_str(code, 120),
        "message": _clip_str(msg, 400),
        "top_violations": top,
        "remediation": remediation,
    }
    # drop empty shells
    if not any([out.get("code"), out.get("message"), out.get("top_violations")]):
        return None
    return out


def _summarize_phase_coverage(ctx: dict[str, Any], *, phase: str | None) -> dict[str, Any] | None:
    attempted = ctx.get("_coverage_attempted")
    if not isinstance(attempted, list) or not attempted:
        return None
    # 注意：当前编排器写入的是 coverage_attempted（不区分成功/失败）；
    # 为与计划文档字段对齐，这里以 “recent attempted unique skills” 近似 skills_succeeded_recent。
    skills: list[str] = []
    seen: set[str] = set()
    successful_skills: set[str] = set()
    for row in attempted[-200:]:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("skill_id") or "").strip()
        if not sid:
            continue
        low = sid.lower()
        status = str(
            row.get("status")
            or row.get("exec_status")
            or row.get("effective_status")
            or ""
        ).strip().lower()
        if status in ("", "success", "ok"):
            successful_skills.add(low)
        if low in seen:
            continue
        seen.add(low)
        skills.append(sid)
        if len(skills) >= 12:
            break
    if not skills:
        return None
    def _truthy(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v > 0
        if isinstance(v, str):
            return v.strip().lower() not in ("", "0", "false", "none", "null")
        if isinstance(v, (list, dict, tuple, set)):
            return len(v) > 0
        return bool(v)

    def _repeat_sig_fatigue(ctx_local: dict[str, Any]) -> bool:
        """action_ledger_recent 里最近 12 条中同 action_signature 出现 >= 3 次视为疲劳。"""
        ledger = ctx_local.get("action_ledger_recent")
        if not isinstance(ledger, list):
            return False
        sig_counts: dict[str, int] = {}
        for row in ledger[-12:]:
            if not isinstance(row, dict):
                continue
            sig = str(row.get("action_signature") or "").strip()
            if not sig:
                continue
            sig_counts[sig] = sig_counts.get(sig, 0) + 1
        return any(c >= 3 for c in sig_counts.values())

    phase_norm = str(phase or "").strip().upper() or None
    recon_markers = {"httpx", "katana", "dirsearch", "whatweb-fingerprint", "http-enum"}
    repeat_risk = False
    if phase_norm == "RECON":
        done = {s.lower() for s in skills}
        repeat_risk = len(done.intersection(recon_markers)) >= 3
    # EXPLOIT 阶段疲劳：在 task-9d0e494a... 实地观测到 12 次连续 EXPLOIT 派发，
    # 且 exploit_ready/confirmed_cve 均已成立，但 LLM 仍未设置 advance_phase=true，
    # 只是把 dispatcher 换成 nuclei/read_workspace_artifact 继续循环。
    # 这里显式暴露 report_ready 信号，并在 guidance 中给出强制指令，推动 LLM 进入 REPORT。
    exploit_ready = False
    report_ready = False
    if phase_norm == "EXPLOIT":
        exploit_ready = any(
            _truthy(ctx.get(k)) for k in ("exploit_ready", "vuln_confirmed", "confirmed_cve", "vulnerability_confirmed")
        )
        # 只要确权已经到手，就让 REPORT 推进信号保持常亮；疲劳检测只作为口气的加码依据。
        report_ready = exploit_ready
    # VULN_SCAN 阶段疲劳 (Fix D4)：在 round 2 thinkphp v1 实地观测到 nuclei/dispatcher/curl-raw
    # 在 vuln 已确认后仍连续派发 10+ 分钟（task-409eea5b... 期间 curl-raw 8x、dispatcher 7x、
    # nuclei 7x），而 LLM 未主动推进 EXPLOIT。这里是 Fix A 的 VULN_SCAN 镜像：
    # 当 vuln_confirmed / exploit_ready 成立 + 同签名疲劳时，强制要求推进 EXPLOIT。
    vuln_confirmed_flag = False
    exploit_pivot_ready = False
    if phase_norm == "VULN_SCAN":
        vuln_confirmed_flag = any(
            _truthy(ctx.get(k))
            for k in ("exploit_ready", "vuln_confirmed", "vulnerability_confirmed", "confirmed_cve")
        )
        exploit_pivot_ready = vuln_confirmed_flag
        vuln_scanner_skills = {"nuclei", "nikto-scan", "web-vuln-pipeline"}
        has_vuln_scan_coverage = bool(successful_skills & vuln_scanner_skills)
    guidance = ""
    if repeat_risk:
        guidance = (
            "Recon coverage looks saturated. Do NOT repeat generic enumeration (httpx/katana/dirsearch/whatweb). "
            "Pivot to evidence-driven validation or advance phase (THREAT_MODEL/VULN_SCAN) if appropriate."
        )
    elif report_ready:
        fatigue = _repeat_sig_fatigue(ctx)
        if fatigue:
            guidance = (
                "Exploit phase objectives are satisfied (exploit_ready / confirmed_cve present) and recent dispatches "
                "are repeating the same action_signature without new evidence. You MUST set "
                "orchestration.advance_phase=true with next_phase=REPORT in this PlanList; do NOT schedule another "
                "EXPLOIT validation cycle."
            )
        else:
            guidance = (
                "Exploit phase objectives are satisfied (exploit_ready / confirmed_cve present). Prefer setting "
                "orchestration.advance_phase=true with next_phase=REPORT unless a concrete, non-redundant exploitation "
                "step is justified."
            )
    elif exploit_pivot_ready:
        fatigue = _repeat_sig_fatigue(ctx)
        if fatigue:
            guidance = (
                "Vuln-scan phase already has confirmed vulnerabilities (vuln_confirmed / exploit_ready present) "
                "and recent dispatches are repeating the same action_signature without new evidence. You MUST set "
                "orchestration.advance_phase=true with next_phase=EXPLOIT in this PlanList; do NOT schedule another "
                "VULN_SCAN validation cycle."
            )
        else:
            guidance = (
                "Vuln-scan phase already has confirmed vulnerabilities (vuln_confirmed / exploit_ready present). "
                "Prefer setting orchestration.advance_phase=true with next_phase=EXPLOIT unless a concrete, "
                "non-redundant scanning step is justified."
            )
    elif phase_norm == "VULN_SCAN" and not has_vuln_scan_coverage:
        guidance = (
            "VULN_SCAN has no vulnerability scanner coverage yet. Do not advance to REPORT. "
            "Choose an available vulnerability scanning skill such as nuclei, nikto-scan, or web-vuln-pipeline "
            "before reporting."
        )
    result: dict[str, Any] = {
        "phase": phase_norm,
        "skills_succeeded_recent": skills,
        "repeat_risk": repeat_risk,
        "guidance": guidance,
    }
    if phase_norm == "EXPLOIT":
        result["exploit_ready"] = exploit_ready
        result["report_ready"] = report_ready
    if phase_norm == "VULN_SCAN":
        result["vuln_confirmed"] = vuln_confirmed_flag
        result["exploit_pivot_ready"] = exploit_pivot_ready
        result["has_vuln_scan_coverage"] = has_vuln_scan_coverage
    return result


def _trim_to_token_budget(items: list[Any], budget_tokens: int) -> list[Any]:
    out: list[Any] = []
    used = 0
    for item in items:
        t = _count_tokens(_json_dump(item))
        if used + t > budget_tokens:
            break
        out.append(item)
        used += t
    return out


def _build_tier0(ctx: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("target", "task_background", "business_background", "extra_user_requirements", "_current_todo"):
        if k in ctx:
            out[k] = ctx[k]
    return out


def _build_tier1(ctx: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    facts = ctx.get("confirmed_facts")
    if isinstance(facts, list):
        out["confirmed_facts"] = [_clip_str(str(x), 400) for x in facts if str(x).strip()][:120]
    board = ctx.get("entity_blackboard")
    if isinstance(board, dict):
        out["entity_blackboard"] = board
    baseline = ctx.get("fallback_baseline")
    if isinstance(baseline, dict):
        out["fallback_baseline"] = baseline
    avoid_sigs = ctx.get("avoid_signatures")
    if isinstance(avoid_sigs, list):
        out["avoid_signatures"] = [str(x) for x in avoid_sigs][:40]
    # 保留系统安全约束
    for k in ("system_error_hint", "skill_fuse_info", "avoid_skills", "loop_break_hint"):
        if k in ctx:
            out[k] = ctx[k]
    # PlanList 业务校验错误摘要：用于下一轮纠错去致盲（避免 replan busy-loop）
    brief = _summarize_plan_list_validation_error_brief(ctx)
    if brief:
        out["plan_list_validation_error_brief"] = brief
    # 覆盖度摘要：用于 RECON fatigue 收敛（尽量短）
    cov = _summarize_phase_coverage(ctx, phase=str(ctx.get("current_phase") or ctx.get("phase") or ""))
    if cov:
        out["phase_coverage_summary"] = cov
    # 路径情报注入：避免后续阶段盲猜 path
    for k in (
        "clustered_targets_preview",
        "clustered_targets_count",
        "clustered_targets_total",
        "clustered_targets_source",
        "asset_path_profile",
        "crawler_extraction_note",
        "framework_path_override",
        "framework_path_override_reason",
    ):
        if k in ctx:
            out[k] = ctx[k]
    return out


def _build_tier2(ctx: dict[str, Any], history_summary: str) -> list[dict[str, Any]]:
    ledger = ctx.get("action_ledger_recent")
    rows: list[dict[str, Any]] = []
    if isinstance(ledger, list):
        for item in ledger[-12:]:
            if not isinstance(item, dict):
                continue
            cp = item.get("canonical_params")
            rows.append(
                {
                    "skill_id": item.get("skill_id"),
                    "target": item.get("target"),
                    "exec_status": item.get("exec_status"),
                    "action_signature": item.get("action_signature"),
                    "params_hint": redact_canonical_params_for_llm(cp if isinstance(cp, dict) else None),
                }
            )
    if history_summary:
        lines = [x for x in history_summary.strip().splitlines() if x.strip()]
        for ln in lines[-6:]:
            rows.append({"history_line": _clip_str(ln, 300)})
    return rows


def _build_tier3(ctx: dict[str, Any], summary_chunks: list[str] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if summary_chunks:
        take = summary_chunks[-_MAX_SUMMARY_CHUNKS:]
        for x in take:
            out.append({"summary": _clip_str(str(x), 500)})
    for k in ("raw_preview", "response_preview", "response_body", "last_memory_path", "kb_hits", "kb_query_text"):
        if k not in ctx:
            continue
        v = ctx.get(k)
        if isinstance(v, str):
            if k in ("raw_preview", "response_preview", "response_body"):
                out.append({k: _clip_str(v, _RAW_PREVIEW_MAX_LEN)})
            else:
                out.append({k: _clip_str(v, 800)})
        else:
            out.append({k: json.loads(_json_dump(v)) if isinstance(v, (dict, list)) else v})

    # nuclei / web-vuln 等工具的 LLM-ready 结构：在保证 token 预算前提下，暴露少量「结构化漏洞证据」给决策层。
    # 为与 System Prompt 中的 wording 对齐，这里直接使用 vulnerabilities / severity_histogram / tech_stack_evidence 这组键名。
    llm_ready = ctx.get("llm_ready")
    if isinstance(llm_ready, dict):
        raw_vr = llm_ready.get("vulnerabilities") or []
        vr_clip: list[Any] = []
        if isinstance(raw_vr, list):
            # 优先保留 high/critical，再补足数量到 10 条
            highs = [v for v in raw_vr if isinstance(v, dict) and str(v.get("severity") or "").lower() in ("high", "critical")]
            others = [v for v in raw_vr if isinstance(v, dict) and v not in highs]
            ordered = highs + others
            vr_clip = ordered[:10]
        sev = llm_ready.get("severity_histogram")
        if not isinstance(sev, dict):
            sev = {}
        tse_raw = llm_ready.get("tech_stack_evidence") or []
        if isinstance(tse_raw, list):
            tse_clip = [str(x) for x in tse_raw[:20]]
        else:
            tse_clip = []
        out.append(
            {
                "vulnerabilities": vr_clip,
                "severity_histogram": sev,
                "tech_stack_evidence": tse_clip,
            }
        )
    # nuclei 严格关键结果：比 llm_ready 更接近原始命中行，供 EXPLOIT 细节判定。
    key_findings = ctx.get("key_findings")
    if isinstance(key_findings, list) and key_findings:
        sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
        normalized: list[dict[str, Any]] = []
        for item in key_findings:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity") or "").strip().lower()
            normalized.append(
                {
                    "severity": sev,
                    "template_id": _clip_str(str(item.get("template_id") or ""), 120),
                    "url": _clip_str(str(item.get("url") or ""), 240),
                    "matcher": _clip_str(str(item.get("matcher") or ""), 200),
                    "evidence": [
                        _clip_str(str(x), 220)
                        for x in (item.get("evidence") or [])[:4]
                        if str(x).strip()
                    ],
                }
            )
        normalized.sort(key=lambda x: sev_rank.get(str(x.get("severity") or "").lower(), -1), reverse=True)
        if normalized:
            out.append({"key_findings": normalized[:12]})
    return out


def build_decision_context(
    target_context: dict[str, Any],
    history_summary: str,
    phase: str | None = None,
    *,
    summary_chunks: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    """按 Tier 0~3 组装上下文，并基于 token 预算淘汰。"""
    ctx = target_context or {}
    # 将 phase 传入 tier1 轻量摘要（避免 tier1 内部猜测）
    if phase:
        ctx = dict(ctx)
        ctx["current_phase"] = phase
    tier0 = _build_tier0(ctx)
    tier1 = _build_tier1(ctx)
    tier2_rows = _build_tier2(ctx, history_summary or "")
    tier3_rows = _build_tier3(ctx, summary_chunks)

    # 先确定 Tier0+1，保证核心约束和事实不丢
    reduced: dict[str, Any] = {"_tier0": tier0}
    # Tier0 already takes some tokens; effective tier1 budget is budget minus tier0 cost
    tier0_tokens = _count_tokens(_json_dump(tier0))
    effective_tier1_budget = max(400, _TIER01_BUDGET - tier0_tokens)
    # Tier1 可能很大，做整体控制
    tier1_text = _json_dump(tier1)
    if _count_tokens(tier1_text) > effective_tier1_budget:
        # 裁剪顺序：系统约束与爬虫产物 → confirmed_facts → blackboard，避免超预算时丢掉 clustered 导致 LLM 幻觉
        trimmed_tier1: dict[str, Any] = {}
        for k in (
            "fallback_baseline",
            "avoid_signatures",
            "system_error_hint",
            "loop_break_hint",
            "plan_list_validation_error_brief",
            "phase_coverage_summary",
        ):
            if k in tier1:
                trimmed_tier1[k] = tier1[k]
        for k in (
            "clustered_targets_preview",
            "clustered_targets_count",
            "clustered_targets_total",
            "clustered_targets_source",
            "asset_path_profile",
            "crawler_extraction_note",
            "framework_path_override",
            "framework_path_override_reason",
            "skill_fuse_info",
            "avoid_skills",
        ):
            if k in tier1:
                trimmed_tier1[k] = tier1[k]
        if isinstance(tier1.get("crawler_confirmed_url_set"), list):
            urls = list(tier1["crawler_confirmed_url_set"])
            trimmed_tier1["crawler_confirmed_url_set"] = urls[: min(48, len(urls))]

        used_pre = _count_tokens(_json_dump(trimmed_tier1))
        fact_budget = max(120, min(int(effective_tier1_budget * 0.5), effective_tier1_budget - used_pre - 280))
        if isinstance(tier1.get("confirmed_facts"), list):
            facts = _trim_to_token_budget(list(tier1.get("confirmed_facts") or []), fact_budget)
            trimmed_tier1["confirmed_facts"] = facts

        if isinstance((tier1.get("entity_blackboard") or {}).get("targets"), dict):
            targets = list(((tier1.get("entity_blackboard") or {}).get("targets") or {}).items())
            compact_targets = {}
            used = _count_tokens(_json_dump(trimmed_tier1))
            for k, v in targets:
                piece = {k: v}
                t = _count_tokens(_json_dump(piece))
                if used + t > effective_tier1_budget:
                    break
                compact_targets[k] = v
                used += t
            trimmed_tier1["entity_blackboard"] = {"targets": compact_targets}
        reduced["_tier1"] = trimmed_tier1
    else:
        reduced["_tier1"] = tier1

    # 硬保护：Tier0+Tier1 是绝对高优，超预算时直接阻断，禁止带病执行
    if _STRICT_TIER01_ASSERT:
        tier01_tokens = _count_tokens(_json_dump({"_tier0": reduced.get("_tier0"), "_tier1": reduced.get("_tier1")}))
        assert tier01_tokens <= _TIER01_BUDGET, (
            f"Tier0+Tier1 token budget exceeded: {tier01_tokens}>{_TIER01_BUDGET}. "
            "Refuse to proceed to avoid losing hard constraints/facts."
        )

    # Tier2 固定预算
    reduced["_tier2"] = _trim_to_token_budget(tier2_rows, _TIER2_BUDGET)

    # Tier3 使用剩余预算
    used = _count_tokens(_json_dump(reduced))
    remaining = max(0, _TOTAL_TOKEN_BUDGET - used)
    reduced["_tier3"] = _trim_to_token_budget(tier3_rows, remaining)

    # 为兼容旧 prompt，提供简化 history 文本（来自 tier2）
    history_lines = []
    for row in reduced.get("_tier2", []):
        if isinstance(row, dict) and row.get("history_line"):
            history_lines.append(str(row.get("history_line")))
        elif isinstance(row, dict):
            history_lines.append(
                f"[{row.get('skill_id')}] target={row.get('target')} status={row.get('exec_status')} sig={str(row.get('action_signature') or '')[:10]}"
            )
    combined = "\n".join(history_lines[-12:])
    if history_summary and not combined:
        # history_summary 追加型日志必须保留末尾最新事件；_clip_str 为 head-biased，
        # 直接用会把最近的 nuclei/exploit 行压成 "[truncated]"（已在 task-0385... 见过）
        if len(history_summary) > 1200:
            combined = "...[truncated]\n" + history_summary[-1200:]
        else:
            combined = history_summary
    return reduced, combined
