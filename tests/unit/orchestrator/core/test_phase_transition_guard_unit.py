import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def _state(phase: str, ctx: dict):
    from app.models import TaskState, Phase

    s = TaskState(task_id="t-guard", name="n", target="http://127.0.0.1:8080")
    s.current_phase = Phase(phase)
    s.target_context.update(ctx or {})
    return s


def test_recon_exit_requires_assets_and_iml():
    from app.enums import Phase
    from app.core.phase_transition_guard import guard_next_phase

    s = _state("RECON", {"information_maturity_level": 0})
    d = guard_next_phase(s, Phase.THREAT_MODEL)
    assert not d.allow
    assert d.blocked
    assert "information_maturity_level>=1" in d.missing


def test_threat_model_sets_unknown_marker_then_can_exit():
    from app.enums import Phase
    from app.core.phase_transition_guard import guard_next_phase

    s = _state(
        "THREAT_MODEL",
        {
            "information_maturity_level": 2,
            "asset_path_profile": {"stack_hint": "unknown"},
        },
    )
    d = guard_next_phase(s, Phase.VULN_SCAN)
    assert d.allow
    assert str(s.target_context.get("framework_target") or "").strip() in ("GENERIC_WEB", "CUSTOM_APP")


def test_exploit_entry_requires_confirmed_cves():
    from app.enums import Phase
    from app.core.phase_transition_guard import guard_next_phase

    s = _state("VULN_SCAN", {"framework_target": "struts2"})
    d = guard_next_phase(s, Phase.EXPLOIT)
    assert not d.allow
    assert d.forced
    assert d.phase == Phase.VULN_SCAN


def test_finish_from_vuln_scan_requires_scanner_coverage_for_web_target():
    from app.core.phase_transition_guard import guard_finish_phase

    s = _state("VULN_SCAN", {"framework_target": "GENERIC_WEB"})
    d = guard_finish_phase(s)
    assert not d.allow
    assert d.blocked
    assert d.phase.value == "VULN_SCAN"
    assert "vuln_scan_coverage" in ",".join(d.missing)


def test_finish_from_vuln_scan_can_report_after_scanner_coverage():
    from app.core.phase_transition_guard import guard_finish_phase

    s = _state("VULN_SCAN", {"framework_target": "GENERIC_WEB"})
    s.coverage_attempted = [{"target": s.target, "skill_id": "nuclei"}]
    d = guard_finish_phase(s)
    assert d.allow
    assert d.forced
    assert d.phase.value == "REPORT"


def test_report_finish_blocks_web_target_without_scanner_coverage():
    from app.core.phase_transition_guard import guard_finish_phase

    s = _state("REPORT", {"framework_target": "GENERIC_WEB"})
    s.coverage_attempted = [{"target": s.target, "skill_id": "curl-raw"}]
    d = guard_finish_phase(s)
    assert not d.allow
    assert d.phase.value == "VULN_SCAN"
    assert "vuln_scan_coverage" in ",".join(d.missing)
