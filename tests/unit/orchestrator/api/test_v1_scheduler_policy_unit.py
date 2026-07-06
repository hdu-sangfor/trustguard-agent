import importlib
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_modules():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        reg_mod = importlib.import_module("app.core.v1_agent_registry")
        pol_mod = importlib.import_module("app.core.v1_scheduler_policy")
        return reg_mod, pol_mod
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_capability_first_prefers_phase_capability():
    reg_mod, pol_mod = _load_modules()
    agents = [
        reg_mod.AgentSpec.from_values("a-recon", "recon", ["recon"], enabled=True),
        reg_mod.AgentSpec.from_values("a-exploit", "exploit", ["exploit"], enabled=True),
    ]
    req = pol_mod.SchedulingRequest(phase="EXPLOIT", task_id="t1")
    out = pol_mod.CapabilityFirstPolicy().choose(req, agents)
    assert [a.agent_id for a in out] == ["a-exploit"]


def test_capability_first_falls_back_when_no_match():
    reg_mod, pol_mod = _load_modules()
    agents = [
        reg_mod.AgentSpec.from_values("a1", "recon", ["recon"], enabled=True),
        reg_mod.AgentSpec.from_values("a2", "scan", ["vuln-scan"], enabled=True),
    ]
    req = pol_mod.SchedulingRequest(phase="REPORT", task_id="t2")
    out = pol_mod.CapabilityFirstPolicy().choose(req, agents)
    assert [a.agent_id for a in out] == ["a1", "a2"]


def test_round_robin_returns_same_set():
    reg_mod, pol_mod = _load_modules()
    agents = [
        reg_mod.AgentSpec.from_values("a1", "recon", ["recon"], enabled=True),
        reg_mod.AgentSpec.from_values("a2", "recon", ["recon"], enabled=True),
    ]
    req = pol_mod.SchedulingRequest(phase="RECON", task_id="task-rr")
    out = pol_mod.RoundRobinPolicy().choose(req, agents)
    assert sorted(a.agent_id for a in out) == ["a1", "a2"]


def test_build_policy_from_env():
    _, pol_mod = _load_modules()
    old = os.environ.get("V1_SCHEDULING_POLICY")
    try:
        os.environ["V1_SCHEDULING_POLICY"] = "round_robin"
        p = pol_mod.build_v1_scheduling_policy_from_env()
        assert p.__class__.__name__ == "RoundRobinPolicy"
    finally:
        if old is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old


def test_role_filter_off_matches_legacy_capability_first():
    reg_mod, pol_mod = _load_modules()
    old = os.environ.get("V1_SCHEDULING_ROLE_FILTER_ENABLED")
    try:
        os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        agents = [
            reg_mod.AgentSpec.from_values("a-exp", "recon", ["exploit"], enabled=True),
            reg_mod.AgentSpec.from_values("b-exp", "exploit", ["exploit"], enabled=True),
        ]
        req = pol_mod.SchedulingRequest(phase="EXPLOIT", task_id="t-role-off")
        out = pol_mod.CapabilityFirstPolicy().choose(req, agents)
        assert sorted(a.agent_id for a in out) == ["a-exp", "b-exp"]
    finally:
        if old is None:
            os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        else:
            os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = old


def test_role_filter_on_narrows_to_exploit_role():
    reg_mod, pol_mod = _load_modules()
    old = os.environ.get("V1_SCHEDULING_ROLE_FILTER_ENABLED")
    try:
        os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = "true"
        agents = [
            reg_mod.AgentSpec.from_values("a-exp", "recon", ["exploit"], enabled=True),
            reg_mod.AgentSpec.from_values("b-exp", "exploit", ["exploit"], enabled=True),
        ]
        req = pol_mod.SchedulingRequest(phase="EXPLOIT", task_id="t-role-on")
        out = pol_mod.CapabilityFirstPolicy().choose(req, agents)
        assert [a.agent_id for a in out] == ["b-exp"]
    finally:
        if old is None:
            os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        else:
            os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = old


def test_role_filter_falls_back_when_no_role_match():
    reg_mod, pol_mod = _load_modules()
    old = os.environ.get("V1_SCHEDULING_ROLE_FILTER_ENABLED")
    try:
        os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = "true"
        agents = [
            reg_mod.AgentSpec.from_values("only-recon", "recon", ["exploit"], enabled=True),
        ]
        req = pol_mod.SchedulingRequest(phase="EXPLOIT", task_id="t-fallback")
        out = pol_mod.CapabilityFirstPolicy().choose(req, agents)
        assert [a.agent_id for a in out] == ["only-recon"]
    finally:
        if old is None:
            os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        else:
            os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = old


def test_bypass_phase_role_and_capability():
    reg_mod, pol_mod = _load_modules()
    old = os.environ.get("V1_SCHEDULING_ROLE_FILTER_ENABLED")
    try:
        os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = "true"
        agents = [
            reg_mod.AgentSpec.from_values("bp", "bypass", ["bypass"], enabled=True),
            reg_mod.AgentSpec.from_values("rx", "recon", ["bypass"], enabled=True),
        ]
        req = pol_mod.SchedulingRequest(phase="BYPASS", task_id="t-bp")
        out = pol_mod.CapabilityFirstPolicy().choose(req, agents)
        assert [a.agent_id for a in out] == ["bp"]
    finally:
        if old is None:
            os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        else:
            os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = old
