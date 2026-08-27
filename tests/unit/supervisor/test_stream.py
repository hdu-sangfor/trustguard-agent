from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app
from app.workflows.pentest import intent


def _events(body: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            parsed.append((event, json.loads("\n".join(data))))
    return parsed


def test_draft_stream_emits_activity_deltas_result_and_done(monkeypatch):
    monkeypatch.setattr(intent, "extract_pentest_draft", lambda _message: None)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/task-agent/draft/stream",
        headers={"X-Actor-Id": "stream-user"},
        json={
            "message": "对 https://test.example.com 做非破坏性渗透，运行20分钟"
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = _events(body)
    names = [name for name, _data in events]
    assert names[0] == "activity"
    assert events[0][1]["status"] == "running"
    assert "delta" in names
    assert names[-2:] == ["result", "done"]
    activity_events = [data for name, data in events if name == "activity"]
    assert [(item["title"], item["status"]) for item in activity_events[:6]] == [
        ("理解任务意图", "running"),
        ("理解任务意图", "done"),
        ("检查目标与安全边界", "running"),
        ("检查目标与安全边界", "done"),
        ("生成任务草稿", "running"),
        ("生成任务草稿", "done"),
    ]

    result = next(data for name, data in events if name == "result")
    text = "".join(data["text"] for name, data in events if name == "delta")
    assert text == result["assistantMessage"]
    assert result["status"] == "NEEDS_CONFIRMATION"
    assert result["draft"]["target"] == "https://test.example.com"
    assert result["draft"]["maxDurationSeconds"] == 1200
    assert result["confirmationToken"]


def test_draft_stream_returns_structured_error_event(monkeypatch):
    from app import main

    monkeypatch.setattr(
        main,
        "_build_draft_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    response = TestClient(app).post(
        "/v1/task-agent/draft/stream",
        headers={"X-Actor-Id": "stream-user"},
        json={"message": "扫描 https://test.example.com"},
    )

    events = _events(response.text)
    assert [name for name, _data in events][-2:] == ["error", "done"]
    assert "boom" not in next(data["message"] for name, data in events if name == "error")


def test_progress_summary_returns_user_facing_message(monkeypatch):
    from app import main

    async def fake_summary(context, fallback):
        assert context["currentPhase"] == "VULN_SCAN"
        assert context["totalSteps"] == 20
        assert "fallbackMessage" not in context
        return "已完成第一轮扫描，正在验证候选入口。"

    monkeypatch.setattr(main, "generate_progress_summary", fake_summary)
    response = TestClient(app).post(
        "/v1/task-agent/progress-summary",
        headers={"X-Actor-Id": "stream-user"},
        json={
            "taskId": "task-1",
            "currentPhase": "VULN_SCAN",
            "totalSteps": 20,
            "recentEvents": [{"eventType": "SKILL_COMPLETED"}],
            "fallbackMessage": "fallback",
        },
    )

    assert response.status_code == 200
    assert response.json()["assistantMessage"] == "已完成第一轮扫描，正在验证候选入口。"
