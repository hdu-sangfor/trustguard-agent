"""v1-micro-sample run.py host-path contract tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

_REPO = REPO_ROOT
_RUN_PY = _REPO / "skills" / "v1-micro-sample" / "run.py"


def test_v1_micro_sample_run_success_writes_workspace_and_stderr_notice(tmp_path: Path) -> None:
    assert _RUN_PY.is_file(), f"missing {_RUN_PY}"
    ws = tmp_path / "ws"
    ws.mkdir()
    payload = {
        "task_id": "t-demo",
        "skill_id": "v1-micro-sample",
        "target": "http://example.com",
        "allowed_target": "http://example.com",
        "params": {"note": "hi"},
        "context": {
            "phase": "RECON",
            "executor_artifact_ref": "wsref:t-demo/RECON/evt-abc_svc",
            "executor_artifact_base": "t-demo/artifacts/RECON/evt-abc_svc",
            "request_id": "req-1",
        },
    }
    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(ws)
    p = subprocess.run(
        [sys.executable, str(_RUN_PY), json.dumps(payload)],
        cwd=str(_RUN_PY.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)

    from tests.executor_test_env import executor_sys_path_isolated

    with executor_sys_path_isolated():
        from app.micro_executor.protocol import ARTIFACT_NOTICE_PREFIX

    assert ARTIFACT_NOTICE_PREFIX in (p.stderr or "")
    data = json.loads((p.stdout or "").strip())
    assert data["status"] == "SUCCESS"
    assert (data.get("parsed_artifacts") or {}).get("artifact_ref", "").startswith("wsref:")
    agent_obj = json.loads(data.get("raw_stdout") or "{}")
    assert agent_obj.get("skill_id") == "v1-micro-sample"
    parsed_path = ws / "t-demo" / "artifacts" / "RECON" / "evt-abc_svc" / "parsed.json"
    assert parsed_path.is_file()
    disk = json.loads(parsed_path.read_text(encoding="utf-8"))
    assert disk.get("note") == "hi"


def test_v1_micro_sample_params_extra_forbidden(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(tmp_path)
    payload = {
        "task_id": "t1",
        "skill_id": "v1-micro-sample",
        "target": "http://a.com",
        "params": {"evil": True},
        "context": {
            "executor_artifact_ref": "wsref:t1/RECON/e",
            "executor_artifact_base": "t1/artifacts/RECON/e",
        },
    }
    p = subprocess.run(
        [sys.executable, str(_RUN_PY), json.dumps(payload)],
        cwd=str(_RUN_PY.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 1
    body = json.loads((p.stdout or "").strip())
    assert body["status"] == "FAILED"


def test_v1_micro_sample_run_py_documents_s01_budget_env_constant() -> None:
    text = _RUN_PY.read_text(encoding="utf-8")
    assert "MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV" in text
    assert "MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS" in text
