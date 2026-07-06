from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import Phase, TaskState


@dataclass
class GuardDecision:
    allow: bool
    phase: Phase
    reason: str
    missing: list[str]
    blocked: bool = False
    forced: bool = False


_PHASE_ROUTE_MATRIX: dict[Phase, set[Phase]] = {
    # RECON 允许直接跳 VULN_SCAN：当 recon 已饱和且 LLM 判断侦察足够时，
    # 强制绕行 THREAT_MODEL 只会浪费一轮 LLM 决策并污染 history。
    # 下方 guard_next_phase 会在跳转时自动执行 _recon_exit_ok + framework 归一化。
    Phase.RECON: {Phase.THREAT_MODEL, Phase.VULN_SCAN},
    Phase.THREAT_MODEL: {Phase.VULN_SCAN},
    Phase.VULN_SCAN: {Phase.EXPLOIT, Phase.REPORT},
    Phase.EXPLOIT: {Phase.REPORT},
    Phase.REPORT: {Phase.DONE},
}


def _ctx(state: TaskState) -> dict[str, Any]:
    return state.target_context if isinstance(state.target_context, dict) else {}


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        s = v.strip().lower()
        return s not in ("", "0", "false", "none", "null")
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) > 0
    return bool(v)


def _has_confirmed_cves(state: TaskState) -> bool:
    ctx = _ctx(state)
    for key in ("confirmed_cves", "confirmed_cve", "vuln_confirmed", "exploit_ready"):
        if _truthy(ctx.get(key)):
            return True
    facts = [str(x).strip().lower() for x in (getattr(state, "confirmed_facts", None) or []) if str(x).strip()]
    for item in facts:
        if "confirmed_cve" in item or "cve-" in item or "vuln_confirmed" in item:
            return True
    return False


_VULN_SCAN_COVERAGE_SKILLS = frozenset(
    {
        "nuclei",
        "nikto-scan",
        "web-vuln-pipeline",
    }
)

_SUCCESS_COVERAGE_STATUSES = frozenset({"", "success", "ok"})


def _coverage_row_ok(item: dict[str, Any]) -> bool:
    status = str(
        item.get("status")
        or item.get("exec_status")
        or item.get("effective_status")
        or ""
    ).strip().lower()
    return status in _SUCCESS_COVERAGE_STATUSES


def coverage_skill_ids(state: TaskState) -> set[str]:
    out: set[str] = set()
    for item in (getattr(state, "coverage_attempted", None) or []):
        if not isinstance(item, dict):
            continue
        if not _coverage_row_ok(item):
            continue
        sid = str(item.get("skill_id") or "").strip().lower()
        if sid:
            out.add(sid)
    ctx = _ctx(state)
    for item in (ctx.get("_coverage_attempted") or []):
        if not isinstance(item, dict):
            continue
        if not _coverage_row_ok(item):
            continue
        sid = str(item.get("skill_id") or "").strip().lower()
        if sid:
            out.add(sid)
    return out


def has_vuln_scan_coverage(state: TaskState) -> bool:
    return bool(coverage_skill_ids(state) & _VULN_SCAN_COVERAGE_SKILLS)


def is_web_target_for_vuln_scan_gate(state: TaskState) -> bool:
    target = str(getattr(state, "target", "") or "").strip().lower()
    if target.startswith(("http://", "https://")):
        return True
    ctx = _ctx(state)
    web_keys = (
        "http-enum_url",
        "http-enum_http_status",
        "curl-raw_http_status",
        "katana_url_counts",
        "url_counts",
        "high_value_endpoints",
        "dispatcher_high_value_endpoints",
    )
    return any(k in ctx for k in web_keys)


def needs_vuln_scan_before_report(state: TaskState) -> bool:
    if not is_web_target_for_vuln_scan_gate(state):
        return False
    return not has_vuln_scan_coverage(state)


def _recon_exit_ok(state: TaskState) -> tuple[bool, list[str]]:
    ctx = _ctx(state)
    missing: list[str] = []
    try:
        iml = int(ctx.get("information_maturity_level") or 0)
    except Exception:
        iml = 0
    # 第一通道：已有爬虫产物或资产画像（原逻辑，适合页面丰富的框架/CMS）
    has_crawl_or_profile = _truthy(ctx.get("clustered_targets_preview")) or _truthy(ctx.get("asset_path_profile"))
    # 第二通道：iml >= 2（L1+L2 已确权：target 可达且 HTTP banner/状态已知）
    # 触发场景：task-c90db15c... (thinkphp 5.0.23) 首页几乎空白，katana 爬不到新链接，
    # asset_path_profile 不成立；但 httpx/http-enum 已拿到 status=200、Server、X-Powered-By 等
    # 服务级线索，足以在 VULN_SCAN 做指纹/模板校验。强行等待 clustered_targets 会死循环 RECON。
    has_service_maturity = iml >= 2
    if not (has_crawl_or_profile or has_service_maturity):
        missing.append("clustered_targets_preview|asset_path_profile|information_maturity_level>=2")
    if iml < 1:
        missing.append("information_maturity_level>=1")
    return len(missing) == 0, missing


def _threat_model_exit_ok(state: TaskState) -> tuple[bool, list[str]]:
    fw = str(_ctx(state).get("framework_target") or "").strip()
    if fw:
        return True, []
    return False, ["framework_target(required; can be explicit unknown marker)"]


def normalize_framework_unknown_if_needed(state: TaskState) -> str:
    ctx = _ctx(state)
    fw = str(ctx.get("framework_target") or "").strip()
    if fw:
        return fw
    # Fix D5：在 fall back 到通用 marker 前，先尝试用 THREAT_MODEL 相同的硬规则/兜底
    # 从 ctx 与 state 的证据中识别具体框架。
    # 实地证据（r3~r7 约 50% 的 thinkphp/struts2 任务跳过 THREAT_MODEL，经 RECON→VULN_SCAN
    # 直跳通道后，此处是唯一能把 framework_target 落到 "struts2" / "thinkphp" 的机会）。
    # 导入放在函数体内以避免 state_machine 导入循环。
    try:
        from app.core.framework_detect import (
            detect_framework_from_context,
            detect_framework_from_state_fallback,
        )
        detected, _ = detect_framework_from_context(ctx)
        if not detected:
            detected, _ = detect_framework_from_state_fallback(state)
        if detected:
            ctx["framework_target"] = detected
            ctx["framework_hint"] = detected
            state.target_context = ctx
            return detected
    except Exception:
        # 识别失败（import 异常 / 异常 ctx 结构）时不要阻断推进，继续走通用 marker。
        pass
    marker = "GENERIC_WEB"
    if _truthy(ctx.get("asset_path_profile")):
        marker = "CUSTOM_APP"
    ctx["framework_target"] = marker
    ctx["framework_hint"] = marker.lower()
    state.target_context = ctx
    return marker


def guard_next_phase(state: TaskState, desired_phase: Phase) -> GuardDecision:
    current = state.current_phase
    if current == desired_phase:
        return GuardDecision(allow=True, phase=desired_phase, reason="same phase", missing=[])

    allowed = _PHASE_ROUTE_MATRIX.get(current, set())
    if desired_phase not in allowed:
        return GuardDecision(
            allow=False,
            phase=current,
            reason=f"phase route blocked: {current.value}->{desired_phase.value} not allowed",
            missing=["whitelist_route"],
            blocked=True,
        )

    if current == Phase.RECON:
        ok, missing = _recon_exit_ok(state)
        if not ok:
            return GuardDecision(
                allow=False,
                phase=current,
                reason="recon exit criteria not met",
                missing=missing,
                blocked=True,
            )
        # RECON 直接跨级进入 VULN_SCAN 时，THREAT_MODEL 的 framework 归一化仍需执行，
        # 否则下一阶段 LLM 拿不到 framework_target / framework_hint，无法选模板。
        if desired_phase == Phase.VULN_SCAN:
            normalize_framework_unknown_if_needed(state)

    if current == Phase.THREAT_MODEL:
        normalize_framework_unknown_if_needed(state)
        ok, missing = _threat_model_exit_ok(state)
        if not ok:
            return GuardDecision(
                allow=False,
                phase=current,
                reason="threat_model exit criteria not met",
                missing=missing,
                blocked=True,
            )

    if current == Phase.VULN_SCAN and desired_phase == Phase.REPORT and needs_vuln_scan_before_report(state):
        return GuardDecision(
            allow=False,
            phase=Phase.VULN_SCAN,
            reason="vuln_scan exit criteria not met; missing vulnerability scanner coverage",
            missing=["vuln_scan_coverage(nuclei|nikto-scan|web-vuln-pipeline)"],
            blocked=True,
        )

    if desired_phase == Phase.EXPLOIT and not _has_confirmed_cves(state):
        return GuardDecision(
            allow=False,
            phase=Phase.VULN_SCAN,
            reason="exploit entry criteria not met; missing confirmed_cves",
            missing=["confirmed_cves"],
            blocked=True,
            forced=True,
        )

    return GuardDecision(allow=True, phase=desired_phase, reason="transition allowed", missing=[])


def guard_finish_phase(state: TaskState) -> GuardDecision:
    if state.current_phase == Phase.REPORT:
        if needs_vuln_scan_before_report(state):
            return GuardDecision(
                allow=False,
                phase=Phase.VULN_SCAN,
                reason="report finish criteria not met; missing vulnerability scanner coverage",
                missing=["vuln_scan_coverage(nuclei|nikto-scan|web-vuln-pipeline)"],
                blocked=True,
                forced=True,
            )
        return GuardDecision(allow=True, phase=Phase.DONE, reason="finish in report", missing=[])
    if state.current_phase == Phase.VULN_SCAN and needs_vuln_scan_before_report(state):
        return GuardDecision(
            allow=False,
            phase=Phase.VULN_SCAN,
            reason="finish criteria not met; missing vulnerability scanner coverage",
            missing=["vuln_scan_coverage(nuclei|nikto-scan|web-vuln-pipeline)"],
            blocked=True,
        )
    return GuardDecision(
        allow=True,
        phase=Phase.REPORT,
        reason="finish rerouted to report",
        missing=[],
        forced=True,
    )
