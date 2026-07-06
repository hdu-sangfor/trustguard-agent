import importlib
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_registry_module():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.core.v1_agent_registry")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_register_and_summary_enabled_only():
    reg_mod = _load_registry_module()
    reg = reg_mod.V1AgentRegistry()
    reg.register(reg_mod.AgentSpec.from_values("agent-exploit", "exploit", ["exploit", "http"], enabled=True))
    reg.register(reg_mod.AgentSpec.from_values("agent-passive", "recon", ["nmap"], enabled=False))
    s = reg.summary()
    assert s["total"] == 2
    assert s["enabled"] == 1
    assert s["agent_ids"] == ["agent-exploit"]
    assert s.get("capability_scores") == {}
    assert s.get("scores_source") == "none"


def test_summary_capability_scores_from_env_enabled_agents_only():
    reg_mod = _load_registry_module()
    old = os.environ.get("V1_AGENT_SCORE_MAP")
    try:
        os.environ["V1_AGENT_SCORE_MAP"] = "agent-exploit:0.75,agent-passive:0.2,unknown:0.99"
        reg = reg_mod.V1AgentRegistry()
        reg.register(reg_mod.AgentSpec.from_values("agent-exploit", "exploit", ["exploit"], enabled=True))
        reg.register(reg_mod.AgentSpec.from_values("agent-passive", "recon", ["nmap"], enabled=False))
        s = reg.summary()
        assert s["scores_source"] == "env"
        assert s["capability_scores"] == {"agent-exploit": 0.75}
    finally:
        if old is None:
            os.environ.pop("V1_AGENT_SCORE_MAP", None)
        else:
            os.environ["V1_AGENT_SCORE_MAP"] = old


def test_summary_ignores_invalid_score_tokens():
    reg_mod = _load_registry_module()
    old = os.environ.get("V1_AGENT_SCORE_MAP")
    try:
        os.environ["V1_AGENT_SCORE_MAP"] = "agent-a:not-a-float,agent-b:0.5"
        reg = reg_mod.V1AgentRegistry()
        reg.register(reg_mod.AgentSpec.from_values("agent-a", "recon", ["http"], enabled=True))
        reg.register(reg_mod.AgentSpec.from_values("agent-b", "recon", ["http"], enabled=True))
        s = reg.summary()
        assert s["capability_scores"] == {"agent-b": 0.5}
    finally:
        if old is None:
            os.environ.pop("V1_AGENT_SCORE_MAP", None)
        else:
            os.environ["V1_AGENT_SCORE_MAP"] = old


def test_find_by_capability_respects_enabled_flag():
    reg_mod = _load_registry_module()
    reg = reg_mod.V1AgentRegistry()
    reg.register(reg_mod.AgentSpec.from_values("a1", "recon", ["nmap", "http"], enabled=True))
    reg.register(reg_mod.AgentSpec.from_values("a2", "recon", ["nmap"], enabled=False))
    ids_enabled = [a.agent_id for a in reg.find_by_capability("nmap", enabled_only=True)]
    ids_all = [a.agent_id for a in reg.find_by_capability("nmap", enabled_only=False)]
    assert ids_enabled == ["a1"]
    assert ids_all == ["a1", "a2"]


def test_build_registry_from_env_parses_enabled_and_disabled():
    reg_mod = _load_registry_module()
    old = os.environ.get("V1_AGENT_REGISTRY")
    try:
        os.environ["V1_AGENT_REGISTRY"] = (
            "agent-exploit:exploit:exploit|http:enabled:container,"
            "agent-passive:recon:nmap:disabled:container"
        )
        reg = reg_mod.build_registry_from_env()
        s = reg.summary()
        assert s["total"] == 2
        assert s["enabled"] == 1
        assert s["agent_ids"] == ["agent-exploit"]
    finally:
        if old is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old
