import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


ROOT = REPO_ROOT
ORCH_ROOT = ROOT / "orchestrator"
if str(ORCH_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCH_ROOT))


def test_default_skill_policy_path_points_to_orchestrator_config(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_SKILL_POLICIES_YAML", raising=False)
    mod = importlib.import_module("app.core.skill_policies_loader")
    mod.reload_skill_policies_for_tests()

    assert mod._default_config_path() == ORCH_ROOT / "config" / "skill_policies.yaml"
    assert mod._default_config_path().is_file()


def test_default_skill_policy_loads_yaml_rules(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_SKILL_POLICIES_YAML", raising=False)
    mod = importlib.import_module("app.core.skill_policies_loader")
    mod.reload_skill_policies_for_tests()

    policy = mod.get_nuclei_policy()

    assert ("apache struts2", ("struts2", "apache", "java")) in policy.tech_tag_mapping
    assert (".action", frozenset({"struts2", "apache", "java"})) in policy.path_url_hints
