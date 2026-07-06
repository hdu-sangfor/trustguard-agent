import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_models():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.models")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_execute_skill_is_normalized_without_legacy_fields():
    models = _load_models()
    d = models.LLMDecisionResponse(
        action_type="EXECUTE_SKILL",
        skill_id="nikto-scan",
        params={"target": "http://127.0.0.1:8080"},
        reasoning="x",
    )
    assert d.action_type == "EXECUTE_SKILLS"
    assert len(d.actions) == 1
    assert d.actions[0].skill_id == "nikto-scan"
    assert d.skill_id is None
    assert d.params is None


def test_memory_fields_normalized_with_backward_compat():
    models = _load_models()
    d = models.LLMDecisionResponse(
        action_type="NEXT_PHASE",
        next_phase="VULN_SCAN",
        updated_facts=["A", "A", "  "],
        facts_to_add=["B"],
        facts_to_remove=["X", "X"],
    )
    assert d.facts_to_add == ["B", "A"]
    assert d.facts_to_remove == ["X"]
    assert d.updated_facts == []


def test_facts_to_add_accepts_dict_objects_as_json_strings():
    models = _load_models()
    d = models.LLMDecisionResponse.model_validate(
        {
            "action_type": "NEXT_PHASE",
            "next_phase": "THREAT_MODEL",
            "reasoning": "x",
            "facts_to_add": [
                {"type": "service", "target": "http://example.com", "service_type": "http"},
                "plain fact",
            ],
            "facts_to_remove": [{"stale": "old"}],
        }
    )
    assert "plain fact" in d.facts_to_add
    assert len(d.facts_to_add) == 2
    structured = [f for f in d.facts_to_add if f != "plain fact"][0]
    assert "service" in structured and "example.com" in structured
    assert len(d.facts_to_remove) == 1
    assert "stale" in d.facts_to_remove[0]
