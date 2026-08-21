"""Evidence FastAPI tests with database calls mocked out."""
import sys
import importlib
from pathlib import Path
from tests.paths import REPO_ROOT
from unittest.mock import patch

# 将 evidence 加入 path 以便 import app
_root = REPO_ROOT
_evidence = _root / "evidence"
for _service in ("executor", "orchestrator", "gateway", "evidence"):
    _path = str(_root / _service)
    while _path in sys.path:
        sys.path.remove(_path)
sys.path.insert(0, str(_evidence))
for _name in list(sys.modules):
    if _name == "app" or _name.startswith("app."):
        del sys.modules[_name]

import pytest

# Evidence 主线为 FastAPI（evidence/app/main.py）。若入口不存在，则跳过以免误用空命名空间 app。
if not (_evidence / "app" / "main.py").is_file():
    pytest.skip(
        "未找到 evidence/app/main.py，Evidence FastAPI 单测跳过。",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

evidence_main = importlib.import_module("app.main")


@pytest.fixture
def mock_db():
    """Mock evidence/app/main.py database helpers to avoid MySQL."""
    def _fake_query(sql: str, params: tuple = ()) -> list[dict]:
        return [{"ok": 1}] if "SELECT 1" in sql else []

    with patch.object(evidence_main, "_query", side_effect=_fake_query):
        with patch.object(evidence_main, "_execute", return_value=1):
            yield


@pytest.fixture
def client(mock_db):
    return TestClient(evidence_main.app)


def test_health(client):
    """GET /health 返回 200 OK。"""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_ingest_event(client):
    """POST /v1/events 接收事件并返回 accepted。"""
    r = client.post(
        "/v1/events",
        json={
            "task_id": "task-abc",
            "timestamp": "2025-01-01T00:00:00Z",
            "event_type": "PHASE_START",
            "source_module": "orchestrator",
            "payload": {"phase": "RECON"},
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("accepted") is True
    assert "event_id" in data


def test_ingest_reasoning_step(client):
    """POST /v1/reasoning-steps 接收 CoT 步骤并返回 accepted。"""
    r = client.post(
        "/v1/reasoning-steps",
        json={
            "task_id": "task-abc",
            "trace_id": "task-abc",
            "step_type": "TASK_PLANNING",
            "status": "SUCCEEDED",
            "summary": "planned",
            "payload": {"item_count": 2},
            "started_at": "2025-01-01T00:00:00Z",
            "finished_at": "2025-01-01T00:00:01Z",
            "duration_ms": 1000,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("accepted") is True
    assert data.get("trace_id") == "task-abc"
    assert "step_id" in data


def test_ingest_reasoning_step_rejects_bad_type(client):
    r = client.post(
        "/v1/reasoning-steps",
        json={
            "task_id": "task-abc",
            "step_type": "NOT_A_REAL_TYPE",
            "status": "SUCCEEDED",
        },
    )
    assert r.status_code == 422


def test_list_reasoning_steps_empty(client):
    r = client.get("/internal/tasks/task-xyz/reasoning-steps")
    assert r.status_code == 200
    assert r.json() == []


def test_get_context_empty(client):
    """GET /internal/tasks/{id}/context 无数据时返回空对象。"""
    r = client.get("/internal/tasks/task-xyz/context")
    assert r.status_code == 200
    assert r.json() == {}


def test_batch_get_contexts_deduplicates_ids_and_returns_decoded_contexts(client):
    captured: dict[str, object] = {}

    def fake_query(sql: str, params: tuple = ()) -> list[dict]:
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "task_id": "at-1",
                "context_json": '{"alert_triage":{"verdict":"suspicious"}}',
            }
        ]

    with patch.object(evidence_main, "_query", side_effect=fake_query):
        r = client.post(
            "/internal/tasks/contexts:batch",
            json={"task_ids": ["at-1", " at-1 ", "at-2"]},
        )

    assert r.status_code == 200
    assert r.json() == {"at-1": {"alert_triage": {"verdict": "suspicious"}}}
    assert captured["params"] == ("at-1", "at-2")
    assert str(captured["sql"]).count("%s") == 2


def test_list_tasks_sorts_skinny_rows_before_reading_large_json(client):
    queries: list[str] = []

    def fake_query(sql: str, params: tuple = ()) -> list[dict]:
        queries.append(sql)
        return []

    with patch.object(evidence_main, "_query", side_effect=fake_query):
        r = client.get("/internal/tasks?limit=200")

    assert r.status_code == 200
    assert r.json() == []
    context_sql = next(sql for sql in queries if "tg_task_context AS ctx" in sql)
    trace_sql = next(sql for sql in queries if "tg_trace_events AS events" in sql)
    assert "SELECT task_id, updated_at" in context_sql
    assert "context_json" not in context_sql.split("FROM (", 1)[1].split(") AS recent", 1)[0]
    assert "SELECT id, created_at" in trace_sql
    assert "payload" not in trace_sql.split("FROM (", 1)[1].split(") AS recent", 1)[0]
