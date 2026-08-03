from __future__ import annotations

import importlib
import sys

import pytest

from tests.paths import REPO_ROOT


def _load_gateway_main():
    gateway_root = str(REPO_ROOT / "gateway")
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, gateway_root)
    try:
        return importlib.import_module("app.main")
    finally:
        sys.path.remove(gateway_root)


def _user(gw):
    return gw.CurrentUser(
        user_id="user-1",
        username="operator",
        role="OPERATOR",
        status="ACTIVE",
    )


@pytest.mark.asyncio
async def test_task_agent_draft_forwards_actor_and_conversation(monkeypatch):
    gw = _load_gateway_main()
    calls = []

    async def fake_supervisor(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"status": "NEEDS_CLARIFICATION", "draftId": None}

    monkeypatch.setattr(gw, "_supervisor", fake_supervisor)
    monkeypatch.setattr(gw, "_record_audit", lambda *_args, **_kwargs: None)

    result = await gw.task_agent_draft(
        gw.TaskAgentDraftRequest(message="scan it", conversationId="conv-1"),
        _user(gw),
    )

    assert result["code"] == "0"
    assert calls == [
        (
            "POST",
            "/v1/task-agent/draft",
            {
                "json_body": {"message": "scan it", "conversationId": "conv-1", "workflowId": "auto"},
                "actor_id": "user-1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_task_agent_confirm_reuses_existing_task_lifecycle(monkeypatch):
    gw = _load_gateway_main()
    calls = {"consume": 0, "complete": 0, "create": 0, "run": 0}
    state = {"completed": False, "status": "PENDING"}

    async def fake_supervisor(_method, path, **_kwargs):
        if path.endswith("/complete"):
            calls["complete"] += 1
            state["completed"] = True
            return {"confirmationState": "COMPLETED", "taskId": "task-1"}
        if path.endswith("/messages"):
            return _kwargs["json_body"]
        calls["consume"] += 1
        return {
            "conversationId": "conv-1",
            "draftId": "draft-1",
            "confirmationState": "COMPLETED" if state["completed"] else "CLAIMED",
            "taskId": "task-1" if state["completed"] else None,
            "draft": {
                "name": "demo",
                "target": "https://test.example.com",
                "maxDurationSeconds": 1200,
            },
        }

    async def fake_create(req, *, actor=None, task_id=None):
        calls["create"] += 1
        assert req.target == "https://test.example.com"
        assert actor.user_id == "user-1"
        assert task_id
        return {"taskId": "task-1", "status": state["status"], "currentPhase": "RECON"}

    async def fake_run(task_id, action, max_ticks=100, max_duration_seconds=None):
        calls["run"] += 1
        state["status"] = "RUNNING"
        assert (task_id, action, max_ticks, max_duration_seconds) == (
            "task-1",
            "run",
            88,
            1200,
        )
        return {"code": "0", "data": None}

    monkeypatch.setattr(gw, "_supervisor", fake_supervisor)
    monkeypatch.setattr(gw, "_create_task_impl", fake_create)
    monkeypatch.setattr(gw, "_run_lifecycle", fake_run)
    monkeypatch.setattr(gw, "_record_audit", lambda *_args, **_kwargs: None)
    request = gw.TaskAgentConfirmRequest(
        confirmationToken="signed-token",
        maxTicks=88,
        idempotencyKey="idem-1",
    )

    first = await gw.task_agent_confirm(request, _user(gw))
    second = await gw.task_agent_confirm(request, _user(gw))

    assert first["data"]["task"]["taskId"] == "task-1"
    assert second["data"]["task"]["taskId"] == "task-1"
    assert calls == {"consume": 2, "complete": 1, "create": 2, "run": 1}


@pytest.mark.asyncio
async def test_task_agent_conversation_forwards_actor(monkeypatch):
    gw = _load_gateway_main()
    captured = {}

    async def fake_supervisor(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return {"conversationId": "conv-1", "messages": [], "taskId": None}

    monkeypatch.setattr(gw, "_supervisor", fake_supervisor)
    result = await gw.task_agent_conversation("conv-1", _user(gw))

    assert result["data"]["conversationId"] == "conv-1"
    assert captured == {
        "method": "GET",
        "path": "/v1/conversations/conv-1",
        "actor_id": "user-1",
    }


@pytest.mark.asyncio
async def test_task_agent_conversation_list_forwards_actor_and_limit(monkeypatch):
    gw = _load_gateway_main()
    captured = {}

    async def fake_supervisor(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return [{"conversationId": "conv-1", "title": "scan target"}]

    monkeypatch.setattr(gw, "_supervisor", fake_supervisor)
    result = await gw.task_agent_conversations(25, _user(gw))

    assert result["data"][0]["conversationId"] == "conv-1"
    assert captured == {
        "method": "GET",
        "path": "/v1/conversations?limit=25",
        "actor_id": "user-1",
    }


@pytest.mark.asyncio
async def test_task_agent_stream_relays_sse_and_closes_upstream(monkeypatch):
    gw = _load_gateway_main()
    closed = {"response": False, "client": False}

    class FakeResponse:
        async def aiter_raw(self):
            yield b'event: delta\ndata: {"text":"hello"}\n\n'
            yield b'event: done\ndata: {}\n\n'

        async def aclose(self):
            closed["response"] = True

    class FakeClient:
        async def aclose(self):
            closed["client"] = True

    async def fake_open(path, *, json_body, actor_id):
        assert path == "/v1/task-agent/draft/stream"
        assert json_body == {"message": "scan it", "conversationId": "conv-1", "workflowId": "auto"}
        assert actor_id == "user-1"
        return FakeClient(), FakeResponse()

    monkeypatch.setattr(gw, "_open_supervisor_stream", fake_open)
    monkeypatch.setattr(gw, "_record_audit", lambda *_args, **_kwargs: None)

    response = await gw.task_agent_draft_stream(
        gw.TaskAgentDraftRequest(message="scan it", conversationId="conv-1"),
        _user(gw),
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert b"".join(chunks).startswith(b"event: delta")
    assert response.media_type == "text/event-stream"
    assert closed == {"response": True, "client": True}


@pytest.mark.asyncio
async def test_task_event_stream_emits_event_status_and_done(monkeypatch):
    gw = _load_gateway_main()
    rows = [
        {"task_id": "task-1", "status": "RUNNING", "current_phase": "RECON"},
        {"task_id": "task-1", "status": "DONE", "current_phase": "DONE"},
    ]

    def fake_row(_task_id):
        return rows.pop(0) if len(rows) > 1 else rows[0]

    async def fake_events(_task_id, _limit):
        return {"data": [{
            "eventId": "evt-1",
            "taskId": "task-1",
            "timestamp": "2026-08-02T00:00:00Z",
            "eventType": "PHASE_START",
            "sourceModule": "orchestrator",
            "payload": {"phase": "RECON"},
        }]}

    async def fake_orch(*_args, **_kwargs):
        return {"taskId": "task-1", "status": "DONE", "currentPhase": "DONE"}

    persisted = []

    async def fake_supervisor(method, path, **kwargs):
        persisted.append((method, path, kwargs))
        return kwargs["json_body"]

    monkeypatch.setattr(gw, "_get_task_row", fake_row)
    monkeypatch.setattr(gw, "task_events", fake_events)
    monkeypatch.setattr(gw, "_orch", fake_orch)
    monkeypatch.setattr(gw, "_sync_task_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gw, "_supervisor", fake_supervisor)
    monkeypatch.setattr(gw, "_record_audit", lambda *_args, **_kwargs: None)

    response = await gw.task_events_stream("task-1", _user(gw), "conv-1")
    body = b"".join([chunk.encode() if isinstance(chunk, str) else chunk async for chunk in response.body_iterator])

    assert b"event: event" in body
    assert b"event: status" in body
    assert b"event: done" in body
    assert b"task-task-1-terminal" in body
    assert len(persisted) == 1
    assert persisted[0][1] == "/v1/conversations/conv-1/messages"
    assert persisted[0][2]["actor_id"] == "user-1"


@pytest.mark.asyncio
async def test_task_event_stream_emits_and_persists_periodic_assistant_reply(monkeypatch):
    gw = _load_gateway_main()
    rows = [
        {"task_id": "task-1", "status": "RUNNING", "current_phase": "RECON"},
        {"task_id": "task-1", "status": "RUNNING", "current_phase": "VULN_SCAN"},
        {"task_id": "task-1", "status": "DONE", "current_phase": "DONE"},
    ]

    def fake_row(_task_id):
        return rows.pop(0) if len(rows) > 1 else rows[0]

    async def fake_events(_task_id, _limit):
        return {"data": [{
            "eventId": f"evt-{index}",
            "taskId": "task-1",
            "timestamp": f"2026-08-02T00:00:{index:02d}Z",
            "eventType": "SKILL_COMPLETED",
            "sourceModule": "orchestrator",
            "payload": {"phase": "VULN_SCAN", "skill_id": f"skill-{index}", "status": "SUCCESS"},
        } for index in range(10)]}

    async def fake_supervisor(_method, path, **kwargs):
        if path == "/v1/task-agent/progress-summary":
            return {"assistantMessage": "已完成一批扫描步骤，正在继续验证。"}
        return kwargs["json_body"]

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(gw, "_get_task_row", fake_row)
    monkeypatch.setattr(gw, "task_events", fake_events)
    monkeypatch.setattr(gw, "_orch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gw, "_sync_task_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gw, "_supervisor", fake_supervisor)
    monkeypatch.setattr(gw, "_record_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gw.asyncio, "sleep", no_sleep)

    response = await gw.task_events_stream("task-1", _user(gw), "conv-1")
    body = b"".join([chunk.encode() if isinstance(chunk, str) else chunk async for chunk in response.body_iterator])

    assert b"event: assistant" in body
    assert "已完成一批扫描步骤".encode() in body
    assert b"event: done" in body
