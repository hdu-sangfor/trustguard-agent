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
                "json_body": {"message": "scan it", "conversationId": "conv-1"},
                "actor_id": "user-1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_task_agent_confirm_reuses_existing_task_lifecycle(monkeypatch):
    gw = _load_gateway_main()
    gw._TASK_AGENT_IDEMPOTENCY.clear()
    calls = {"consume": 0, "create": 0, "run": 0}

    async def fake_supervisor(_method, _path, **_kwargs):
        calls["consume"] += 1
        return {
            "conversationId": "conv-1",
            "draftId": "draft-1",
            "draft": {
                "name": "demo",
                "target": "https://test.example.com",
                "maxDurationSeconds": 1200,
            },
        }

    async def fake_create(req, *, actor=None):
        calls["create"] += 1
        assert req.target == "https://test.example.com"
        assert actor.user_id == "user-1"
        return {"taskId": "task-1", "status": "PENDING", "currentPhase": "RECON"}

    async def fake_run(task_id, action, max_ticks=100, max_duration_seconds=None):
        calls["run"] += 1
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
    assert second == first
    assert calls == {"consume": 1, "create": 1, "run": 1}
