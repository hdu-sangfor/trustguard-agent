"""state_machine：LLM 产出 skill 别名时应可映射到真实可用 skill_id。"""

import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_state_machine():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.core.state_machine")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_resolve_skill_alias_web_fingerprint():
    sm = _load_state_machine()
    out = sm._resolve_skill_id_alias("web-fingerprint", ["http-enum", "whatweb-fingerprint"])
    assert out == "whatweb-fingerprint"


def test_resolve_skill_alias_no_mapping_keeps_raw():
    sm = _load_state_machine()
    out = sm._resolve_skill_id_alias("unknown-skill", ["http-enum", "whatweb-fingerprint"])
    assert out == "unknown-skill"


def test_sanitize_execute_actions_maps_alias_and_collects_invalid():
    sm = _load_state_machine()
    actions = [
        sm.ActionItem(skill_id="web-fingerprint", params={"target": "http://x"}),
        sm.ActionItem(skill_id="non-existent", params={}),
    ]
    sanitized, invalid, alias_mapped = sm._sanitize_execute_actions(
        actions,
        ["whatweb-fingerprint", "http-enum"],
    )
    assert len(sanitized) == 1
    assert sanitized[0].skill_id == "whatweb-fingerprint"
    assert invalid == ["non-existent"]
    assert alias_mapped == [{"from": "web-fingerprint", "to": "whatweb-fingerprint"}]

