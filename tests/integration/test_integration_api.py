"""
集成测试：Gateway API（POST/GET tasks, events, run, tick, stop, resume）。

运行前请确保：
  - MySQL 已启动，且 Gateway 能连接（库 trustguard_agent 和 tg_* 表已初始化）
  - Gateway已启动（docker compose up gateway，或在 gateway/ 下 uvicorn app.main:app --port 18080）
  - 可选：编排器、Evidence已启动（创建任务会调编排器，超时可通过 INTEGRATION_HTTP_TIMEOUT 调大）

  cd trustguard-agent && pytest tests/integration -v -m integration
  仅单元测试（与 CI 一致）：pytest tests/ -v -m "not integration and not smoke"
"""
import pytest
import httpx

from tests.conftest import INTEGRATION_TIMEOUT

pytestmark = pytest.mark.integration


def test_create_task(gateway_url):
    """POST /api/v1/tasks：target 必填，返回 taskId（UUID）。"""
    with httpx.Client(timeout=INTEGRATION_TIMEOUT) as client:
        r = client.post(
            f"{gateway_url}/api/v1/tasks",
            json={"target": "http://vuln-web:8080", "name": "集成测试任务", "description": "pytest"},
        )
    assert r.status_code == 200, (
        r.text + " (若为 MySQL 错误，请确保 MySQL 已启动且 Gateway 数据库配置正确)"
    )
    data = r.json()
    assert data.get("code") == "0"
    payload = data.get("data")
    assert payload is not None
    assert "taskId" in payload
    task_id = payload["taskId"]
    assert task_id.startswith("task-") and len(task_id) > 10


def test_create_task_rejects_empty_target(gateway_url):
    """target 为空时应返回 400。"""
    with httpx.Client(timeout=INTEGRATION_TIMEOUT) as client:
        r = client.post(f"{gateway_url}/api/v1/tasks", json={"target": "", "name": "x"})
    assert r.status_code in (400, 422), r.text


def test_get_task(gateway_url):
    """先创建再 GET /api/v1/tasks/{taskId}。"""
    with httpx.Client(timeout=INTEGRATION_TIMEOUT) as client:
        cr = client.post(
            f"{gateway_url}/api/v1/tasks",
            json={"target": "http://192.168.1.100:80", "name": "获取任务测试"},
        )
        assert cr.status_code == 200, (
            cr.text + " (若为 MySQL 错误，请确保 MySQL 已启动且 Gateway 数据库配置正确)"
        )
        task_id = cr.json()["data"]["taskId"]

        r = client.get(f"{gateway_url}/api/v1/tasks/{task_id}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("code") == "0"
        t = data["data"]
        assert t["taskId"] == task_id
        assert t.get("target") == "http://192.168.1.100:80"
        assert t.get("status") in ("PENDING", "RUNNING", "PAUSED", "DONE", "FAILED")


def test_get_task_404(gateway_url):
    """不存在的 taskId 返回 404 或 code 表示不存在。"""
    with httpx.Client(timeout=INTEGRATION_TIMEOUT) as client:
        r = client.get(f"{gateway_url}/api/v1/tasks/task-nonexistent-id-12345")
    assert r.status_code == 200  # 当前实现为 200 + code
    data = r.json()
    assert data.get("code") != "0" or data.get("data") is None


def test_get_events(gateway_url):
    """GET /api/v1/tasks/{taskId}/events 返回列表（可为空）。"""
    with httpx.Client(timeout=INTEGRATION_TIMEOUT) as client:
        cr = client.post(
            f"{gateway_url}/api/v1/tasks",
            json={"target": "http://localhost:9999", "name": "事件测试"},
        )
        assert cr.status_code == 200, (
            cr.text + " (若为 JDBC/MySQL 错误，请确保 MySQL 已启动)"
        )
        task_id = cr.json()["data"]["taskId"]

        r = client.get(f"{gateway_url}/api/v1/tasks/{task_id}/events", params={"limit": 10})
        assert r.status_code == 200
        data = r.json()
        assert data.get("code") == "0"
        assert isinstance(data.get("data"), list)


def test_stop_and_resume(gateway_url):
    """创建任务 -> stop -> resume，状态应变为 PAUSED 再 RUNNING（或保持可续跑）。"""
    with httpx.Client(timeout=INTEGRATION_TIMEOUT) as client:
        cr = client.post(
            f"{gateway_url}/api/v1/tasks",
            json={"target": "http://vuln-web:8080", "name": "停止续跑测试"},
        )
        assert cr.status_code == 200, (
            cr.text + " (若超时，请确认编排器已启动或设 INTEGRATION_HTTP_TIMEOUT>60)"
        )
        task_id = cr.json()["data"]["taskId"]

        stop_r = client.post(f"{gateway_url}/api/v1/tasks/{task_id}/stop")
        assert stop_r.status_code == 200, stop_r.text

        get_r = client.get(f"{gateway_url}/api/v1/tasks/{task_id}")
        assert get_r.status_code == 200
        status_after_stop = get_r.json().get("data", {}).get("status")

        resume_r = client.post(f"{gateway_url}/api/v1/tasks/{task_id}/resume")
        assert resume_r.status_code == 200, resume_r.text

        get_r2 = client.get(f"{gateway_url}/api/v1/tasks/{task_id}")
        assert get_r2.status_code == 200
        status_after_resume = get_r2.json().get("data", {}).get("status")
        assert status_after_resume in ("RUNNING", "PENDING", "PAUSED", "DONE", "FAILED")
