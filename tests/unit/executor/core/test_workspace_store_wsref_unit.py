import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def test_prepare_artifact_slot_and_write_reuse(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    root = REPO_ROOT
    executor_root = str(root / "executor")
    sys.path.insert(0, executor_root)
    try:
        import app.core.workspace_store as ws

        ws = importlib.reload(ws)
    finally:
        if executor_root in sys.path:
            sys.path.remove(executor_root)

    ref, base, event_id = ws.prepare_artifact_slot(
        task_id="task-abc",
        phase="VULN_SCAN",
        skill_id="web-vuln-pipeline",
    )
    assert ref.startswith("wsref:task-abc/VULN_SCAN/evt-")
    assert base.exists()
    assert event_id.startswith("evt-")

    out_ref = ws.write_artifact(
        task_id="task-abc",
        phase="VULN_SCAN",
        skill_id="web-vuln-pipeline",
        request_payload={"k": "v"},
        status="SUCCESS",
        duration_ms=123,
        raw_stdout="out",
        raw_stderr="err",
        parsed_artifacts={"foo": "bar"},
        artifact_base=base,
        artifact_ref_precomputed=ref,
    )
    assert out_ref == ref

    payload = ws.read_artifact(ref, include_raw=True)
    assert payload is not None
    assert payload["artifact_ref"] == ref
    assert payload["meta"]["event_id"] == event_id
    assert payload["meta"]["skill_id"] == "web-vuln-pipeline"
    assert payload["parsed"]["foo"] == "bar"
    assert payload["raw_stdout"] == "out"
    assert payload["raw_stderr"] == "err"
