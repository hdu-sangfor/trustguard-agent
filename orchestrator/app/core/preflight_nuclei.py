"""nuclei：tags 归一化、狙击推导、IML 钳制、scan_mode / single_url（策略与编排核心解耦）。"""
from __future__ import annotations

import os
from typing import Any

from app.core.agent_tools import get_fingerprint_text_for_pipeline
from app.core.http_enum_seeds import normalize_url_for_pipeline
from app.core.preflight_context import PreflightContext
from app.core.skill_params_normalize import (
    dedupe_tags_preserve_order,
    normalize_tags_to_list,
    tags_list_to_nuclei_cli,
)
from app.core.skill_policies_loader import get_nuclei_policy

_SNIFFER_ON = (os.getenv("ORCHESTRATOR_NUCLEI_SNIPER_PREFLIGHT", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_UNKNOWN_FRAMEWORK_MARKERS = {"generic_web", "custom_app", "unknown"}
_FRAMEWORK_SPECIFIC_BLOCK = {
    "struts",
    "struts2",
    "thinkphp",
    "spring",
    "weblogic",
    "shiro",
    "fastjson",
}
_GENERIC_WEB_BASELINE = ["misconfig", "exposure", "sqli", "lfi", "rce", "xss", "ssrf", "redirect"]


def _normalize_rate_limit_param(raw: Any, default: int = 50) -> int:
    """编排器侧兜底：规范 nuclei rate_limit，避免把不可解析值传给执行层。"""
    if raw is None:
        return max(1, default)
    if isinstance(raw, bool):
        return max(1, default)
    if isinstance(raw, (int, float)):
        return max(1, int(raw))
    s = str(raw).strip().lower()
    if not s:
        return max(1, default)
    preset = {
        "low": 10,
        "slow": 10,
        "medium": 30,
        "normal": 50,
        "high": 80,
        "fast": 100,
    }
    if s in preset:
        return preset[s]
    try:
        return max(1, int(float(s)))
    except (ValueError, TypeError):
        return max(1, default)


def _append_ctx_tech_lists(ctx: dict[str, Any], parts: list[str]) -> None:
    def add(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
        elif isinstance(v, dict):
            for k in ("signal", "name", "template_id", "url", "severity", "text", "message"):
                x = v.get(k)
                if x:
                    parts.append(str(x))
        elif isinstance(v, list):
            for x in v[:120]:
                add(x)

    pl_tech = ctx.get("pipeline_tech_stack_evidence")
    if isinstance(pl_tech, list) and pl_tech:
        for item in pl_tech[:120]:
            add(item)
    else:
        add(ctx.get("tech_stack_evidence"))
        for k, v in ctx.items():
            if isinstance(k, str) and "tech_stack_evidence" in k.lower():
                add(v)
    sh = ctx.get("stack_hint")
    if isinstance(sh, str) and sh.strip():
        parts.append(sh.strip())


def _collect_signal_urls(ctx: dict[str, Any]) -> list[str]:
    pl_urls = ctx.get("pipeline_signal_urls")
    if isinstance(pl_urls, list) and pl_urls:
        out: list[str] = []
        seen: set[str] = set()
        for u in pl_urls:
            s = str(u).strip()
            if s.startswith(("http://", "https://")) and s not in seen:
                seen.add(s)
                out.append(s)
        return out[:240]

    urls: list[str] = []
    seen: set[str] = set()

    def push(u: str) -> None:
        u = str(u).strip()
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            urls.append(u)

    for key, v in ctx.items():
        if not isinstance(key, str):
            continue
        kl = key.lower()
        if "suspicious_signals" not in kl and "high_value" not in kl:
            continue
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get("url"):
                    push(str(it["url"]))
    for hv in (ctx.get("dispatcher_high_value_endpoints"), ctx.get("high_value_endpoints")):
        if isinstance(hv, list):
            for u in hv:
                if isinstance(u, str):
                    push(u)
    return urls[:240]


def _path_hints_from_policy(urls: list[str], policy: Any) -> set[str]:
    out: set[str] = set()
    for raw in urls:
        u = raw.lower()
        for contains, tag_set in policy.path_url_hints:
            if contains.lower() in u:
                out |= set(tag_set)
    return out


def derive_dynamic_nuclei_tags(ctx: dict[str, Any]) -> tuple[set[str], dict[str, Any]]:
    policy = get_nuclei_policy()
    meta: dict[str, Any] = {"sources": []}
    parts: list[str] = []
    _append_ctx_tech_lists(ctx, parts)
    fp = get_fingerprint_text_for_pipeline(ctx)
    if fp:
        parts.append(fp)
    blob = "\n".join(parts).lower()
    out: set[str] = set()
    if blob.strip():
        for match_key, tag_tup in policy.tech_tag_mapping:
            if match_key in blob:
                out.update(tag_tup)
                meta["sources"].append(f"keyword:{match_key}")
    urls = _collect_signal_urls(ctx)
    path_tags = _path_hints_from_policy(urls, policy)
    if path_tags:
        out |= path_tags
        meta["sources"].append("url_path_hints")
    return out, meta


def clamp_nuclei_tags_for_iml(
    iml: int,
    requested_tags: list[str] | None,
    *,
    extra_allow: list[str] | None = None,
) -> tuple[list[str], bool]:
    policy = get_nuclei_policy()
    iml_key = min(max(iml, 1), 4)
    cap_list = list(policy.iml_tag_sets.get(iml_key) or policy.iml_tag_sets.get(2) or [])
    extra = normalize_tags_to_list(extra_allow)
    allowed_ordered = dedupe_tags_preserve_order(cap_list + extra)

    canon_by_lower: dict[str, str] = {}
    for t in allowed_ordered:
        tl = t.lower()
        if tl not in canon_by_lower:
            canon_by_lower[tl] = t

    if iml <= 0:
        return list(allowed_ordered), True

    req = normalize_tags_to_list(requested_tags)
    if not req:
        return list(allowed_ordered), False

    kept: list[str] = []
    for t in req:
        tl = t.lower()
        if tl in canon_by_lower:
            kept.append(canon_by_lower[tl])

    if not kept:
        return list(allowed_ordered), True

    clamped = kept != req
    return kept, clamped


def apply_nuclei_preflight(params: dict[str, Any], pctx: PreflightContext) -> None:
    policy = get_nuclei_policy()
    ctx = pctx.ctx
    iml = pctx.iml
    seeds = pctx.seeds

    params.setdefault("target", pctx.state.target)
    # 向技能层透传框架情报；优先 Threat Model 写入的 framework_target
    fw = str(ctx.get("framework_target") or ctx.get("framework_hint") or "").strip().lower()
    def _has_action_signal() -> bool:
        candidates: list[str] = []
        for k in ("target", "single_url", "url"):
            v = params.get(k)
            if isinstance(v, str) and v.strip():
                candidates.append(v)
        for k in ("target", "framework_signal_urls"):
            v = ctx.get(k)
            if isinstance(v, str) and v.strip():
                candidates.append(v)
            elif isinstance(v, list):
                candidates.extend(str(x) for x in v[:80] if str(x).strip())
        blob = " ".join(candidates).lower()
        return ".action" in blob
    if not fw:
        fp_blob = (get_fingerprint_text_for_pipeline(ctx) or "").lower()
        if ".action" in fp_blob or "struts" in fp_blob or _has_action_signal():
            fw = "struts2"
    if fw:
        params.setdefault("framework_hint", fw)
        params.setdefault("framework_target", fw)

    # 按 phase 物理隔离 scan/exploit 模式，交给 skill preflight 追加 safe-poc/exploit tags
    phase_name = str(getattr(pctx.state.current_phase, "value", "") or "").upper()
    if phase_name == "EXPLOIT":
        params["mode"] = "exploit"
    else:
        params.setdefault("mode", "scan")
    params["rate_limit"] = _normalize_rate_limit_param(params.get("rate_limit"), default=50)
    raw_in = params.get("tags")
    if raw_in is None:
        raw_in = params.get("nuclei_tags")
    tags_list = normalize_tags_to_list(raw_in)
    tags_requested_before_sniper = list(tags_list)
    extra_allow_list: list[str] = []

    if _SNIFFER_ON:
        dyn, sniper_meta = derive_dynamic_nuclei_tags(ctx)
        if dyn:
            for t in policy.sniper_boost_tags:
                dyn.add(t)
            tags_list = sorted(dyn)
            try:
                rl = int(os.getenv("ORCHESTRATOR_NUCLEI_SNIPER_RATE_LIMIT", "30"))
            except ValueError:
                rl = 30
            params["rate_limit"] = max(1, rl)
            params["nuclei_sniper_preflight"] = True
            params["nuclei_preflight_sources"] = list(sniper_meta.get("sources") or [])
            params["nuclei_preflight_diagnostics"] = (
                "Pre-flight Nuclei sniper: tags narrowed to "
                f"{tags_list_to_nuclei_cli(tags_list)}; sources={sniper_meta.get('sources')}"
            )
            extra_allow_list = list(tags_list)
            cap_env = (os.getenv("ORCHESTRATOR_NUCLEI_SNIPER_TIMEOUT_CAP") or "").strip()
            if cap_env:
                try:
                    cap_timeout = int(cap_env)
                except ValueError:
                    cap_timeout = 0
                if cap_timeout > 0:
                    cur_t = int(params.get("timeout") or params.get("nuclei_timeout") or 120)
                    params["timeout"] = min(cur_t, cap_timeout)
                    params["nuclei_timeout"] = min(int(params.get("nuclei_timeout") or cur_t), cap_timeout)

    eff_list, clamped = clamp_nuclei_tags_for_iml(iml, tags_list, extra_allow=extra_allow_list)
    params["tags"] = eff_list
    params["nuclei_tags"] = eff_list
    if clamped:
        params["nuclei_tags_clamped_by_iml"] = True
        params["nuclei_tags_requested"] = tags_requested_before_sniper

    # phase 级硬隔离：VULN_SCAN 以 safe-poc 为基线，EXPLOIT 以 exploit 为基线；
    # 同时并入决策请求/策略推导出的标签，提高模板命中概率。
    mode_raw = str(params.get("mode") or "").strip().lower()
    base_tags = ["exploit"] if mode_raw == "exploit" else ["safe-poc"]

    requested = normalize_tags_to_list(params.get("nuclei_tags_requested") or params.get("nuclei_tags") or params.get("tags"))
    eff: list[str] = []
    seen: set[str] = set()
    for t in base_tags + requested:
        s = str(t).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        eff.append(s)

    # 无指纹降级：禁止框架特异模板，锁定通用 Web 风险面基线。
    fw_marker = str((params.get("framework_target") or params.get("framework_hint") or fw or "")).strip().lower()
    if fw_marker in _UNKNOWN_FRAMEWORK_MARKERS:
        filtered: list[str] = []
        seen_filtered: set[str] = set()
        for tag in eff:
            tk = str(tag).strip().lower()
            if not tk or tk in _FRAMEWORK_SPECIFIC_BLOCK:
                continue
            if tk in seen_filtered:
                continue
            seen_filtered.add(tk)
            filtered.append(tk)
        for base in _GENERIC_WEB_BASELINE:
            if base not in seen_filtered:
                filtered.append(base)
                seen_filtered.add(base)
        eff = filtered
        params["nuclei_unknown_framework_profile"] = True

    params["tags"] = eff
    params["nuclei_tags"] = eff

    run_id_key = pctx.run_id_key
    rid = str(params.get("run_id") or ctx.get(run_id_key) or "").strip()
    has_chunk = bool(params.get("chunk_index") or params.get("chunk_path"))
    explicit_mode = str(params.get("nuclei_scan_mode") or "").strip().lower()
    tgt_u = str(params.get("target") or pctx.state.target or "").strip()
    has_discovery_dir = bool(str(params.get("output_discovery_dir") or params.get("output_dir") or "").strip())

    if explicit_mode == "direct":
        params["nuclei_scan_mode"] = "direct"
        # 有高价值 seeds 时优先使用 seed[0]，否则退回任务 target
        su = seeds[0] if seeds else tgt_u
        params.setdefault("single_url", normalize_url_for_pipeline(su))
    elif has_discovery_dir and not has_chunk:
        # 使用共享 discovery/clustered_targets.txt 作为 -l 输入，不强依赖 dispatcher chunk。
        params["nuclei_scan_mode"] = "direct"
        params.setdefault("single_url", normalize_url_for_pipeline(tgt_u))
    elif explicit_mode in ("manifest", "manifest_chunk"):
        params["nuclei_scan_mode"] = "manifest_chunk"
        if rid and not has_chunk:
            params.setdefault("chunk_index", 1)
    elif rid and has_chunk:
        params["nuclei_scan_mode"] = "manifest_chunk"
    elif rid:
        params["nuclei_scan_mode"] = "manifest_chunk"
        params.setdefault("chunk_index", 1)
    elif seeds:
        params["nuclei_scan_mode"] = "direct"
        params["single_url"] = normalize_url_for_pipeline(str(seeds[0]))
    else:
        params["nuclei_scan_mode"] = "direct"
        params["single_url"] = normalize_url_for_pipeline(tgt_u)

    # 框架识别不应只影响模板路径，还应提升路径深度探测密度（由 skill preflight 落地）。
    if str(fw).strip().lower() == "struts2":
        params.setdefault("path_depth_profile", "struts2-action-heavy")
