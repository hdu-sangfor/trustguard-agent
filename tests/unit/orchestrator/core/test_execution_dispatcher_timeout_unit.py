import importlib
import os
import sys

import pytest

from tests.paths import REPO_ROOT


def _load_dispatcher_module():
    orch_root = str(REPO_ROOT / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.core.execution_dispatcher")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_poll_timeout_extends_for_web_vuln_pipeline():
    os.environ["MQ_POLL_TIMEOUT_SECONDS"] = "120"
    os.environ["MQ_POLL_TIMEOUT_WEB_VULN_SECONDS"] = "720"
    mod = _load_dispatcher_module()
    timeout = mod._derive_poll_timeout_seconds("web-vuln-pipeline", {"timeout": 180})
    assert timeout >= 720


def test_poll_timeout_respects_requested_timeout_with_buffer():
    os.environ["MQ_POLL_TIMEOUT_SECONDS"] = "120"
    mod = _load_dispatcher_module()
    timeout = mod._derive_poll_timeout_seconds("http-enum", {"timeout": 240})
    assert timeout >= 300


def test_poll_timeout_extends_for_katana_pipeline():
    os.environ["MQ_POLL_TIMEOUT_SECONDS"] = "120"
    os.environ["MQ_POLL_TIMEOUT_WEB_PIPELINE_SECONDS"] = "600"
    mod = _load_dispatcher_module()
    assert mod._derive_poll_timeout_seconds("katana", {}) >= 600
    assert mod._derive_poll_timeout_seconds("dirsearch", {}) >= 600


@pytest.mark.asyncio
async def test_http_dispatcher_uses_upstream_request_id(monkeypatch):
    mod = _load_dispatcher_module()
    orch_root = str(REPO_ROOT / "orchestrator")
    sys.path.insert(0, orch_root)
    try:
        exec_client_mod = importlib.import_module("app.clients.executor_client")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)

    class _Store:
        def __init__(self):
            self.started = []

        async def try_acquire_task_inflight(self, task_id, lim):
            return True

        async def release_task_inflight(self, task_id):
            return None

        async def try_acquire_rate_slot(self, scope_key, lim):
            return True

        async def release_rate_slot(self, scope_key):
            return None

        async def register_execution_start(self, request_id, task_id, skill_id, todo_id):
            self.started.append(request_id)
            return True

        async def register_execution_finish(self, request_id, task_id, status, artifact_ref):
            return None

    async def _fake_call_executor(**kwargs):
        return mod.ExecuteSkillResponse(status="SUCCESS", parsed_artifacts={}, duration_ms=1)

    store = _Store()
    monkeypatch.setattr(mod, "_TASK_STORE", store)
    monkeypatch.setattr(exec_client_mod, "call_executor", _fake_call_executor)
    disp = mod.HttpExecutionDispatcher()
    out = await disp.dispatch(
        task_id="t1",
        skill_id="http-enum",
        target="http://127.0.0.1:8080",
        params={},
        allowed_target="http://127.0.0.1:8080",
        context={"request_id": "req-fixed-1"},
    )
    assert out.request_id == "req-fixed-1"
    assert store.started == ["req-fixed-1"]


@pytest.mark.asyncio
async def test_http_and_mq_dispatcher_e2e_lite_for_skill(monkeypatch):
    mod = _load_dispatcher_module()
    orch_root = str(REPO_ROOT / "orchestrator")
    sys.path.insert(0, orch_root)
    try:
        exec_client_mod = importlib.import_module("app.clients.executor_client")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)

    class _Store:
        def __init__(self):
            self.started = []

        async def try_acquire_task_inflight(self, task_id, lim):
            return True

        async def release_task_inflight(self, task_id):
            return None

        async def try_acquire_rate_slot(self, scope_key, lim):
            return True

        async def release_rate_slot(self, scope_key):
            return None

        async def register_execution_start(self, request_id, task_id, skill_id, todo_id):
            self.started.append((request_id, skill_id))
            return True

        async def register_execution_finish(self, request_id, task_id, status, artifact_ref):
            return None

    class _Rec:
        status = "SUCCESS"
        artifact_ref = "wsref:t-r4gc/RECON/evt-xx_nmap"
        worker_id = "w-1"
        finished_at = "2026-04-07T00:00:00Z"

    async def _fake_call_executor(**kwargs):
        return mod.ExecuteSkillResponse(
            status="SUCCESS",
            parsed_artifacts={"skill_id": "nmap"},
            duration_ms=2,
            request_id=kwargs.get("context", {}).get("request_id"),
        )

    async def _fake_poll(request_id, timeout_seconds=None, poll_interval_seconds=None):
        return _Rec()

    async def _fake_load_parsed(artifact_ref, attempts=4, wait_seconds=0.25):
        return {"skill_id": "nmap", "_artifact_ref": artifact_ref}

    published = {"n": 0}

    def _fake_publish(msg):
        published["n"] += 1

    store = _Store()
    monkeypatch.setattr(mod, "_TASK_STORE", store)
    monkeypatch.setattr(exec_client_mod, "call_executor", _fake_call_executor)
    monkeypatch.setattr(mod, "_poll_until_execution_done", _fake_poll)
    monkeypatch.setattr(mod, "_load_parsed_with_retry", _fake_load_parsed)
    monkeypatch.setattr(mod, "_publish_mq_execute_task", _fake_publish)

    http_disp = mod.HttpExecutionDispatcher()
    http_out = await http_disp.dispatch(
        task_id="t-r4gc-http",
        skill_id="nmap",
        target="http://example.com",
        params={"timeout": 5},
        allowed_target="http://example.com",
        context={"request_id": "req-http-r4gc", "phase": "RECON"},
    )
    assert http_out.status == "SUCCESS"
    assert (http_out.parsed_artifacts or {}).get("skill_id") == "nmap"

    mq_disp = mod.MqExecutionDispatcher()
    mq_out = await mq_disp.dispatch(
        task_id="t-r4gc-mq",
        skill_id="nmap",
        target="http://example.com",
        params={"timeout": 5},
        allowed_target="http://example.com",
        context={"request_id": "req-mq-r4gc", "phase": "RECON"},
    )
    assert mq_out.status == "SUCCESS"
    assert (mq_out.parsed_artifacts or {}).get("skill_id") == "nmap"
    assert published["n"] == 1
