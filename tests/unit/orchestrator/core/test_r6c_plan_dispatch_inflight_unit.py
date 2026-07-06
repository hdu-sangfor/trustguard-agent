"""R6c：多 Plan 项/多路派发并发时与 TaskStore 在途背压对齐（派发层 CAS/锁）。"""
import asyncio
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH_ROOT = REPO_ROOT / "orchestrator"
if str(_ORCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_ORCH_ROOT))


@pytest.mark.asyncio
async def test_concurrent_http_dispatches_same_task_second_gets_inflight_backpressure(monkeypatch):
    """模拟同一 task 两条「计划派发」交叠：在途=1 时第二路在 call_executor 之前失败。"""
    import app.clients.executor_client as executor_client
    import app.core.execution_dispatcher as dispatcher_mod
    from app.core.execution_dispatcher import HttpExecutionDispatcher
    from app.core.task_store import InMemoryTaskStore
    from app.models import ExecuteSkillResponse

    monkeypatch.setenv("MAX_IN_FLIGHT_SKILLS_PER_TASK", "1")

    store = InMemoryTaskStore()
    monkeypatch.setattr(dispatcher_mod, "_TASK_STORE", store)

    exec_calls = 0

    async def _slow_executor(**kwargs):
        nonlocal exec_calls
        exec_calls += 1
        await asyncio.sleep(0.15)
        return ExecuteSkillResponse(
            status="SUCCESS",
            parsed_artifacts={"artifact_ref": "art-1"},
            raw_stdout=None,
            raw_stderr=None,
            duration_ms=1,
        )

    monkeypatch.setattr(executor_client, "call_executor", _slow_executor)

    d = HttpExecutionDispatcher()

    async def _d1():
        return await d.dispatch(
            task_id="task-plan",
            skill_id="nmap",
            target="http://127.0.0.1",
            params={},
            allowed_target="http://127.0.0.1",
            context={"request_id": "req-r6c-1", "plan_id": "p1"},
        )

    async def _d2():
        await asyncio.sleep(0.02)
        return await d.dispatch(
            task_id="task-plan",
            skill_id="nmap",
            target="http://127.0.0.1",
            params={},
            allowed_target="http://127.0.0.1",
            context={"request_id": "req-r6c-2", "plan_id": "p2"},
        )

    a, b = await asyncio.gather(_d1(), _d2())

    statuses = {a.status, b.status}
    assert "SUCCESS" in statuses
    assert "FAILED" in statuses
    failed = a if a.status == "FAILED" else b
    err = (failed.parsed_artifacts or {}).get("error")
    assert err == "MAX_IN_FLIGHT_SKILLS_PER_TASK"
    assert exec_calls == 1
