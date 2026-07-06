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


def test_loop_signature_contains_target_dimension():
    sm = _load_state_machine()
    p = {"a": 1}
    h = sm.artifact_hash({"ok": True})
    s1 = "|".join(["nuclei", "http://a", sm.canonical_params(p), h])
    s2 = "|".join(["nuclei", "http://b", sm.canonical_params(p), h])
    assert s1 != s2
