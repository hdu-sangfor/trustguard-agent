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


def test_resolve_read_target_list_prefers_task_katana_urls_rel():
    sm = _load_state_machine()
    out = sm._resolve_read_target_list_rel_path(
        task_id="task-abc",
        params={"rel_path": "task-abc/RECON/evt-123_katana/katana_urls.txt"},
        target_context={
            "diagnostics": {
                "task_katana_urls_rel": "task-abc/web-vuln/run123/discovery/katana_urls.txt",
            }
        },
        shared_discovery_dir="task-abc/web-vuln/run123/discovery",
    )
    assert out == "task-abc/web-vuln/run123/discovery/katana_urls.txt"


def test_resolve_read_target_list_fallback_shared_discovery_when_invalid():
    sm = _load_state_machine()
    out = sm._resolve_read_target_list_rel_path(
        task_id="task-xyz",
        params={"rel_path": "task-other/RECON/evt-1/katana_urls.txt"},
        target_context={},
        shared_discovery_dir="task-xyz/web-vuln/run999/discovery",
    )
    assert out == "task-xyz/web-vuln/run999/discovery/katana_urls.txt"

