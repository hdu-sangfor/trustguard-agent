"""decision_policy：VULN_SCAN + 指纹信号时对 web-vuln-pipeline 的列表顺序偏好。"""

import importlib
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
        models_mod = importlib.import_module("app.models")
        agent_tools_mod = importlib.import_module("app.core.agent_tools")
        policy_mod = importlib.import_module("app.core.decision_policy")
        return models_mod, agent_tools_mod, policy_mod
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_fingerprint_signals_present_matches_extract():
    _models_mod, agent_tools_mod, _ = _load_modules()
    assert not agent_tools_mod.fingerprint_signals_present({})
    assert agent_tools_mod.fingerprint_signals_present({"fingerprints": ["Apache"]})


def test_bias_moves_web_vuln_pipeline_when_fingerprint_present():
    models_mod, _, policy_mod = _load_modules()
    bias = policy_mod.bias_available_skills_for_decision
    phase = models_mod.Phase.VULN_SCAN
    base = ["nikto-scan", "web-vuln-pipeline", "nuclei"]
    ctx = {"whatweb-fingerprint_fingerprints": "WordPress 6.0"}
    out = bias(phase, base, ctx)
    assert out[0] == "web-vuln-pipeline"
    assert set(out) == set(base)


def test_bias_noop_without_fingerprint():
    models_mod, _, policy_mod = _load_modules()
    bias = policy_mod.bias_available_skills_for_decision
    phase = models_mod.Phase.VULN_SCAN
    base = ["a", "web-vuln-pipeline", "b"]
    assert bias(phase, base, {}) == base


def test_bias_noop_wrong_phase():
    models_mod, _, policy_mod = _load_modules()
    bias = policy_mod.bias_available_skills_for_decision
    phase = models_mod.Phase.RECON
    base = ["web-vuln-pipeline", "x"]
    assert bias(phase, base, {"fingerprints": ["x"]}) == base
