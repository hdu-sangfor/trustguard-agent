"""
单元测试：VULN_SCAN 阶段疲劳检测 (Fix D4) — Fix A 的镜像。

实地证据 (round 2 thinkphp v1, task-409eea5ba87c498f8ee8caf8feca235c):
- VULN_SCAN 阶段连续派发 compile_trace=30: curl-raw×8, dispatcher×7, nuclei×7
- 在 vuln 已确认后仍然持续循环 10+ 分钟，LLM 未主动推进 EXPLOIT
- action_ledger_recent 里多个 action_signature 出现 >= 3 次

D4 = Fix A 的结构化镜像：
- phase=VULN_SCAN + (exploit_ready|vuln_confirmed|confirmed_cve|vulnerability_confirmed) → exploit_pivot_ready=True
- 再叠加同签名疲劳 → guidance 强制 advance_phase=true,next_phase=EXPLOIT
"""
import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def _ledger_with_repeat(sig: str, count: int) -> list[dict]:
    return [
        {
            "ts": 1700000000 + i,
            "skill_id": "nuclei",
            "target": "http://host.docker.internal:8080",
            "exec_status": "SUCCESS",
            "action_signature": sig,
            "canonical_params": {"target": "http://host.docker.internal:8080", "timeout": 300},
        }
        for i in range(count)
    ]


def _coverage(n: int = 5) -> list[dict]:
    return [
        {"target": "http://host.docker.internal:8080", "skill_id": "nuclei"}
        for _ in range(n)
    ]


def test_vuln_scan_ready_exposes_exploit_pivot_flag():
    """VULN_SCAN + vuln_confirmed 时必须输出 exploit_pivot_ready=True。"""
    from app.core.decision_context import _summarize_phase_coverage

    ctx = {
        "_coverage_attempted": _coverage(),
        "vuln_confirmed": True,
        "action_ledger_recent": [],
    }
    out = _summarize_phase_coverage(ctx, phase="VULN_SCAN")
    assert out is not None
    assert out["phase"] == "VULN_SCAN"
    assert out["vuln_confirmed"] is True
    assert out["exploit_pivot_ready"] is True
    g = out["guidance"] or ""
    assert "EXPLOIT" in g
    assert "advance_phase" in g


def test_vuln_scan_fatigue_upgrades_guidance_to_must():
    """VULN_SCAN + vuln_confirmed + 同签名 >= 3 → guidance 带 MUST 级别指令。"""
    from app.core.decision_context import _summarize_phase_coverage

    sig = "abcdef1234567890abcdef1234567890"
    ctx = {
        "_coverage_attempted": _coverage(),
        "exploit_ready": True,
        "action_ledger_recent": _ledger_with_repeat(sig, 4),
    }
    out = _summarize_phase_coverage(ctx, phase="VULN_SCAN")
    assert out is not None
    assert out["exploit_pivot_ready"] is True
    g = out["guidance"] or ""
    assert "MUST" in g
    assert "EXPLOIT" in g
    assert "advance_phase=true" in g


def test_vuln_scan_without_confirmation_does_not_set_pivot_ready():
    """VULN_SCAN 但没有确权 → 不触发 pivot_ready。"""
    from app.core.decision_context import _summarize_phase_coverage

    ctx = {
        "_coverage_attempted": _coverage(),
        # 无 vuln_confirmed / exploit_ready / confirmed_cve
    }
    out = _summarize_phase_coverage(ctx, phase="VULN_SCAN")
    assert out is not None
    assert out.get("vuln_confirmed") is False
    assert out.get("exploit_pivot_ready") is False
    assert (out.get("guidance") or "") == ""


def test_vuln_scan_without_scanner_coverage_guides_scanner_before_report():
    """VULN_SCAN 只有准备类/探测类工具时，提示 planner 先跑漏洞扫描而不是进 REPORT。"""
    from app.core.decision_context import _summarize_phase_coverage

    ctx = {
        "_coverage_attempted": [
            {"target": "http://host.docker.internal:8080", "skill_id": "dispatcher"},
            {"target": "http://host.docker.internal:8080", "skill_id": "curl-raw"},
        ],
    }

    out = _summarize_phase_coverage(ctx, phase="VULN_SCAN")

    assert out is not None
    assert out["has_vuln_scan_coverage"] is False
    guidance = out.get("guidance") or ""
    assert "Do not advance to REPORT" in guidance
    assert "nuclei" in guidance


def test_vuln_scan_failed_scanner_still_guides_scanner_before_report():
    """失败的漏洞扫描不应满足 VULN_SCAN 覆盖门禁。"""
    from app.core.decision_context import _summarize_phase_coverage

    ctx = {
        "_coverage_attempted": [
            {"target": "http://host.docker.internal:8080", "skill_id": "nuclei", "status": "FAILED"},
        ],
    }

    out = _summarize_phase_coverage(ctx, phase="VULN_SCAN")

    assert out is not None
    assert out["has_vuln_scan_coverage"] is False
    assert "Do not advance to REPORT" in (out.get("guidance") or "")


def test_vuln_scan_confirmed_cve_list_triggers_pivot():
    """confirmed_cve 非空列表也应触发 pivot_ready。"""
    from app.core.decision_context import _summarize_phase_coverage

    ctx = {
        "_coverage_attempted": _coverage(),
        "confirmed_cve": ["CVE-2018-20062"],  # ThinkPHP 5.0.23 RCE
        "action_ledger_recent": [],
    }
    out = _summarize_phase_coverage(ctx, phase="VULN_SCAN")
    assert out is not None
    assert out["exploit_pivot_ready"] is True
    assert "EXPLOIT" in (out["guidance"] or "")


def test_exploit_phase_still_gets_report_ready_not_pivot():
    """回归：Fix A 的 EXPLOIT 路径必须保留 — exploit_ready/report_ready 字段，不出现 pivot_ready。"""
    from app.core.decision_context import _summarize_phase_coverage

    ctx = {
        "_coverage_attempted": _coverage(),
        "exploit_ready": True,
        "action_ledger_recent": [],
    }
    out = _summarize_phase_coverage(ctx, phase="EXPLOIT")
    assert out is not None
    assert out["phase"] == "EXPLOIT"
    assert out["exploit_ready"] is True
    assert out["report_ready"] is True
    # EXPLOIT 场景下不应出现 VULN_SCAN-only 字段
    assert "exploit_pivot_ready" not in out
    assert "vuln_confirmed" not in out  # 键名不同；Fix D4 VULN_SCAN 才暴露
    # guidance 应引导 REPORT 而不是 EXPLOIT
    g = out.get("guidance") or ""
    assert "REPORT" in g


def test_recon_phase_still_not_touched_by_d4():
    """回归：RECON 疲劳语义不受 D4 影响。"""
    from app.core.decision_context import _summarize_phase_coverage

    cov = [
        {"target": "http://x:80", "skill_id": sid}
        for sid in ("httpx", "katana", "dirsearch", "whatweb-fingerprint")
    ]
    ctx = {"_coverage_attempted": cov, "vuln_confirmed": True}
    out = _summarize_phase_coverage(ctx, phase="RECON")
    assert out is not None
    assert out["phase"] == "RECON"
    assert out["repeat_risk"] is True
    # RECON 场景下不应出现 VULN_SCAN-only 字段
    assert "exploit_pivot_ready" not in out
