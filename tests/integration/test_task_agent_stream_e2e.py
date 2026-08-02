"""Compose-level E2E coverage for the natural-language task Supervisor path."""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

import httpx
import pytest

from tests.conftest import INTEGRATION_TIMEOUT


pytestmark = pytest.mark.integration


def _sse_events(lines: Iterator[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    event_name = "message"
    data_lines: list[str] = []
    for line in lines:
        if not line:
            if data_lines:
                yield event_name, json.loads("\n".join(data_lines))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event_name, json.loads("\n".join(data_lines))


def _login(client: httpx.Client, gateway_url: str) -> dict[str, str]:
    response = client.post(
        f"{gateway_url}/api/v1/auth/login",
        json={"username": "operator", "password": "operator123"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("code") == "0", response.text
    return {"Authorization": f"Bearer {payload['data']['token']}"}


def test_compose_task_agent_stream_confirmation_and_task_events(gateway_url: str) -> None:
    timeout = httpx.Timeout(INTEGRATION_TIMEOUT, read=max(INTEGRATION_TIMEOUT, 30.0))
    with httpx.Client(timeout=timeout) as client:
        headers = _login(client, gateway_url)
        with client.stream(
            "POST",
            f"{gateway_url}/api/v1/task-agent/draft/stream",
            headers={**headers, "Accept": "text/event-stream"},
            json={
                "message": (
                    "请对 https://test.example.com 执行非破坏性安全扫描，"
                    "不要利用漏洞，最多运行 5 分钟"
                ),
                "workflowId": "auto",
            },
        ) as response:
            assert response.status_code == 200, response.read().decode(errors="replace")
            events = list(_sse_events(response.iter_lines()))

        names = [name for name, _data in events]
        assert "activity" in names
        assert "delta" in names
        assert names[-2:] == ["result", "done"]
        result = next(data for name, data in events if name == "result")
        assert result["status"] == "NEEDS_CONFIRMATION"
        assert result["workflowId"] == "pentest"
        assert result["draft"]["allowExploit"] is False
        token = result["confirmationToken"]

        confirmation_body = {
            "confirmationToken": token,
            "idempotencyKey": "compose-e2e-confirmation",
            "start": False,
        }
        first = client.post(
            f"{gateway_url}/api/v1/task-agent/confirm",
            headers=headers,
            json=confirmation_body,
        )
        second = client.post(
            f"{gateway_url}/api/v1/task-agent/confirm",
            headers=headers,
            json=confirmation_body,
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        first_data = first.json()["data"]
        second_data = second.json()["data"]
        task_id = first_data["task"]["taskId"]
        assert second_data["task"]["taskId"] == task_id
        assert first_data["task"]["executionPolicy"]["allow_exploit"] is False

        history = client.get(
            f"{gateway_url}/api/v1/task-agent/conversations/{result['conversationId']}",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        conversation = history.json()["data"]
        assert conversation["taskId"] == task_id
        assert [message["role"] for message in conversation["messages"][:2]] == ["user", "assistant"]
        assert sum(
            message["id"] == f"confirm-{result['draftId']}"
            for message in conversation["messages"]
        ) == 1

        with client.stream(
            "GET",
            f"{gateway_url}/api/v1/tasks/{task_id}/events/stream",
            headers={**headers, "Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200, response.read().decode(errors="replace")
            received_status = False
            for event_name, data in _sse_events(response.iter_lines()):
                if event_name == "status":
                    assert data["taskId"] == task_id
                    received_status = True
                    break
            assert received_status

    supervisor_url = os.getenv("OVERRIDE_SUPERVISOR_URL", "http://localhost:18082")
    health = httpx.get(f"{supervisor_url}/health", timeout=10.0)
    assert health.status_code == 200, health.text
    assert health.json()["storeBackend"] == "redis"
    workflows = httpx.get(f"{supervisor_url}/v1/workflows", timeout=10.0)
    assert workflows.status_code == 200, workflows.text
    assert any(item.get("workflowId") == "pentest" for item in workflows.json())
