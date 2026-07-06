"""
单元测试：Fix D6 _maybe_upgrade_framework_target — 把 marker 升级为具体框架。

实地证据 (round 8 thinkphp): D5 在 RECON→VULN_SCAN 转场时运行，那时
tech_stack_evidence 还未被 nuclei 填充，导致 framework_target 落到 GENERIC_WEB。
D6 在每个 tick 开头再评估，把 marker 升级为具体框架（thinkphp/struts2/spring）。
"""
import os
import sys
import asyncio
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def _make_state(phase: str, ctx: dict):
    from app.models import TaskState, Phase

    s = TaskState(task_id="t-d6", name="n", target="http://host.docker.internal:8080")
    s.current_phase = Phase(phase)
    s.target_context.update(ctx)
    return s


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_running() else asyncio.run(coro)


def test_upgrade_generic_web_to_thinkphp_when_evidence_arrives(monkeypatch):
    """GENERIC_WEB marker + 后续 tech_stack_evidence 包含 thinkphp → 升级为 thinkphp。"""
    from app.core import state_machine

    s = _make_state("VULN_SCAN", {
        "framework_target": "GENERIC_WEB",
        "framework_hint": "generic_web",
        "tech_stack_evidence": [
            {"signal": "php", "template_id": "ThinkPHP Framework Fingerprint", "url": "http://x/"},
        ],
    })

    emitted = []
    async def _fake_emit(evt):
        emitted.append(evt)
    monkeypatch.setattr(state_machine, "_emit", _fake_emit)

    asyncio.run(state_machine._maybe_upgrade_framework_target(s))
    assert s.target_context.get("framework_target") == "thinkphp"
    assert s.target_context.get("framework_hint") == "thinkphp"
    assert any(getattr(e, 'event_type', None) == "FRAMEWORK_TARGET_UPGRADED" for e in emitted)


def test_upgrade_custom_app_to_struts2(monkeypatch):
    """CUSTOM_APP marker + .action URL → 升级为 struts2。"""
    from app.core import state_machine

    s = _make_state("EXPLOIT", {
        "framework_target": "CUSTOM_APP",
        "high_value_endpoints": ["http://host/ajax/bind.action"],
    })
    async def _fake_emit(evt): pass
    monkeypatch.setattr(state_machine, "_emit", _fake_emit)

    asyncio.run(state_machine._maybe_upgrade_framework_target(s))
    assert s.target_context.get("framework_target") == "struts2"


def test_no_downgrade_when_already_specific(monkeypatch):
    """已是具体框架（struts2）时 D6 不改动，避免反复翻转。"""
    from app.core import state_machine

    s = _make_state("VULN_SCAN", {
        "framework_target": "struts2",
        "tech_stack_evidence": [{"signal": "php"}],  # 即使有噪声证据
    })
    emitted = []
    async def _fake_emit(evt): emitted.append(evt)
    monkeypatch.setattr(state_machine, "_emit", _fake_emit)

    asyncio.run(state_machine._maybe_upgrade_framework_target(s))
    assert s.target_context.get("framework_target") == "struts2"  # 不变
    assert not emitted  # 不发 UPGRADED 事件


def test_no_upgrade_when_no_evidence(monkeypatch):
    """marker 仍是 GENERIC_WEB 但无任何有效 evidence → 保持不变，不发事件。"""
    from app.core import state_machine

    s = _make_state("VULN_SCAN", {"framework_target": "GENERIC_WEB"})
    emitted = []
    async def _fake_emit(evt): emitted.append(evt)
    monkeypatch.setattr(state_machine, "_emit", _fake_emit)

    asyncio.run(state_machine._maybe_upgrade_framework_target(s))
    assert s.target_context.get("framework_target") == "GENERIC_WEB"
    assert not emitted


def test_upgrade_empty_to_specific_via_confirmed_facts(monkeypatch):
    """framework_target 为空 + confirmed_facts 里有关键词 → 兜底升级。"""
    from app.core import state_machine

    s = _make_state("VULN_SCAN", {"framework_target": ""})
    s.confirmed_facts = ["confirmed_cve: thinkphp 5.0.23 captcha RCE"]
    async def _fake_emit(evt): pass
    monkeypatch.setattr(state_machine, "_emit", _fake_emit)

    asyncio.run(state_machine._maybe_upgrade_framework_target(s))
    assert s.target_context.get("framework_target") == "thinkphp"


def test_upgrade_unknown_marker(monkeypatch):
    """'unknown' marker 也应被视作可升级。"""
    from app.core import state_machine

    s = _make_state("VULN_SCAN", {
        "framework_target": "unknown",
        "pipeline_tech_stack_evidence": [{"template_id": "spring-boot-actuator-env"}],
    })
    async def _fake_emit(evt): pass
    monkeypatch.setattr(state_machine, "_emit", _fake_emit)

    asyncio.run(state_machine._maybe_upgrade_framework_target(s))
    assert s.target_context.get("framework_target") == "spring"
