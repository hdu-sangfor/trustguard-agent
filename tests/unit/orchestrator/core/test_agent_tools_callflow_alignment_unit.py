import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_agent_tools():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.core.agent_tools")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_non_error_statuses_are_terminal_only():
    mod = _load_agent_tools()
    assert "ok" in mod._NON_ERROR_STATUSES
    assert "success" in mod._NON_ERROR_STATUSES
    assert "dispatched" not in mod._NON_ERROR_STATUSES


def test_resolve_memory_event_id_uses_request_or_random():
    mod = _load_agent_tools()

    mid = mod._resolve_memory_event_id(
        artifact_ref="",
        call_ctx_request_id="req-abc",
        exec_result_request_id=None,
    )
    assert mid == "evt-req-abc"

    mid2 = mod._resolve_memory_event_id(
        artifact_ref="",
        call_ctx_request_id=None,
        exec_result_request_id=None,
    )
    assert mid2.startswith("evt-")
    assert len(mid2) > 6


def test_extract_artifact_returncode_prefers_parsed_artifacts():
    mod = _load_agent_tools()

    class _ExecResult:
        parsed_artifacts = {"returncode": "2"}

    rc = mod._extract_artifact_returncode(_ExecResult(), {"returncode": 0})
    assert rc == 2


def test_extract_artifact_returncode_fallback_to_resolved_artifacts():
    mod = _load_agent_tools()
    rc = mod._extract_artifact_returncode(None, {"returncode": 1})
    assert rc == 1
