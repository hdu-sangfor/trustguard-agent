import asyncio
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
        agent_base_mod = importlib.import_module("app.core.agent_base")
        return models_mod, agent_base_mod
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_agent_base_call_decision_engine_uses_biased_skill_order():
    models_mod, agent_base_mod = _load_modules()
    captured: dict[str, list[str]] = {}

    async def _fake_call_decision_engine(**kwargs):
        captured["available_skill_ids"] = list(kwargs.get("available_skill_ids") or [])
        return models_mod.LLMDecisionResponse(action_type="FINISH", reasoning="ok"), None

    class _NoopAgent(agent_base_mod.BasePentestAgent):
        async def _handle_decision(self, **kwargs):
            return None

    state = models_mod.TaskState(task_id="t1", name="n", target="http://x")
    state.current_phase = models_mod.Phase.VULN_SCAN
    state.target_context["whatweb-fingerprint_fingerprints"] = "WordPress"

    old = agent_base_mod.call_decision_engine
    try:
        agent_base_mod.call_decision_engine = _fake_call_decision_engine
        agent = _NoopAgent(state)
        asyncio.run(
            agent._call_decision_engine(
                ["nikto-scan", "web-vuln-pipeline", "nuclei"]
            )
        )
    finally:
        agent_base_mod.call_decision_engine = old

    ids = captured.get("available_skill_ids") or []
    assert ids[0] == "web-vuln-pipeline"
    assert set(ids) == {"nikto-scan", "web-vuln-pipeline", "nuclei"}
