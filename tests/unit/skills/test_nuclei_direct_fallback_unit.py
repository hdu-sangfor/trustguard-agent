"""nuclei execute.py: direct-mode fallback when payload.target is an http URL without run_id."""
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from tests.paths import REPO_ROOT

_NUCLEI_ROOT = REPO_ROOT / "skills" / "nuclei"
_NUCLEI_SCRIPTS = str(_NUCLEI_ROOT / "scripts")
_NUCLEI_COMMON = str(_NUCLEI_ROOT / "common")


@contextmanager
def _nuclei_sys_path():
    """Temporarily add nuclei paths to sys.path without leaking into other tests."""
    added = []
    for p in (_NUCLEI_SCRIPTS, _NUCLEI_COMMON):
        if p not in sys.path:
            sys.path.insert(0, p)
            added.append(p)
    try:
        yield
    finally:
        for p in added:
            try:
                sys.path.remove(p)
            except ValueError:
                pass
        # Evict any nuclei-specific modules loaded during the context so they
        # don't shadow katana's katana_io in subsequent tests.
        to_evict = [k for k in sys.modules if k in ("katana_io", "workspace_manifest", "nuclei_io",
                                                      "workspace_resolve", "postflight_nuclei",
                                                      "fingerprint_tags", "etl", "nuclei_execute")]
        for k in to_evict:
            sys.modules.pop(k, None)


def _load_nuclei_execute():
    script = _NUCLEI_ROOT / "scripts" / "execute.py"
    spec = importlib.util.spec_from_file_location("nuclei_execute", str(script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_direct_fallback_via_payload_target(monkeypatch):
    """payload.target=http URL with no run_id must route to direct scan, not fail with 'params.run_id required'."""
    with _nuclei_sys_path():
        mod = _load_nuclei_execute()

        direct_called = []

        def _fake_direct(payload, start):
            direct_called.append(payload)
            out = {
                "status": "SUCCESS",
                "parsed_artifacts": {"findings": []},
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": 0,
            }
            print(json.dumps(out))
            return 0

        monkeypatch.setattr(mod, "_run_direct_scan", _fake_direct)

        payload = {
            "task_id": "t-nuclei-test",
            "target": "http://host.docker.internal:8080",
            "params": {},
        }
        monkeypatch.setattr(sys, "argv", ["execute.py", json.dumps(payload)])
        rc = mod.main()
    assert rc == 0, "expected direct scan success (rc=0)"
    assert len(direct_called) == 1, "expected _run_direct_scan to be called once"


def test_no_direct_fallback_when_run_id_present(monkeypatch, tmp_path):
    """When run_id is set, must NOT go through direct — should fail with manifest error."""
    with _nuclei_sys_path():
        mod = _load_nuclei_execute()

        direct_called = []

        def _fake_direct(payload, start):
            direct_called.append(payload)
            return 0

        monkeypatch.setattr(mod, "_run_direct_scan", _fake_direct)
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

        payload = {
            "task_id": "t-nuclei-test2",
            "target": "http://host.docker.internal:8080",
            "params": {"run_id": "run-abc123", "chunk_index": 1},
        }
        monkeypatch.setattr(sys, "argv", ["execute.py", json.dumps(payload)])
        mod.main()
    # Direct scan must not be called when run_id is present
    assert len(direct_called) == 0, "direct scan must not be called when run_id is present"


def test_direct_fallback_via_params_url(monkeypatch):
    """params.url=http URL with no run_id must also trigger direct scan."""
    with _nuclei_sys_path():
        mod = _load_nuclei_execute()

        direct_called = []

        def _fake_direct(payload, start):
            direct_called.append(payload)
            out = {
                "status": "SUCCESS",
                "parsed_artifacts": {"findings": []},
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": 0,
            }
            print(json.dumps(out))
            return 0

        monkeypatch.setattr(mod, "_run_direct_scan", _fake_direct)

        payload = {
            "task_id": "t-nuclei-test3",
            "target": "",
            "params": {"url": "http://example.com"},
        }
        monkeypatch.setattr(sys, "argv", ["execute.py", json.dumps(payload)])
        rc = mod.main()
    assert rc == 0
    assert len(direct_called) == 1
