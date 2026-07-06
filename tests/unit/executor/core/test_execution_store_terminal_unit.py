"""MQ Worker execution_store：终态 record 跳过执行（R6b）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ROOT = REPO_ROOT
_EXEC_STORE = _ROOT / "executor" / "app" / "execution_store.py"


def _load_execution_store():
    spec = importlib.util.spec_from_file_location("executor_execution_store", str(_EXEC_STORE))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def test_execution_record_is_terminal():
    es = _load_execution_store()
    assert es.execution_record_is_terminal({"status": "SUCCESS"}) is True
    assert es.execution_record_is_terminal({"status": "ok"}) is True
    assert es.execution_record_is_terminal({"status": "FAILED"}) is True
    assert es.execution_record_is_terminal({"status": "TIMEOUT"}) is True
    assert es.execution_record_is_terminal({"status": "SKIPPED_DUPLICATE_REQUEST"}) is True
    assert es.execution_record_is_terminal({"finished_at": "2024-01-01T00:00:00Z"}) is True
    assert es.execution_record_is_terminal({"status": "RUNNING"}) is False
    assert es.execution_record_is_terminal({"status": "PENDING"}) is False
    assert es.execution_record_is_terminal({}) is False


@pytest.mark.asyncio
async def test_register_execution_start_skips_when_redis_record_terminal(monkeypatch):
    import redis.asyncio as redis_asyncio

    es = _load_execution_store()

    class FakeClient:
        async def exists(self, key: str) -> int:
            return 1

        async def hgetall(self, key: str) -> dict:
            return {
                "task_id": "t1",
                "skill_id": "nmap",
                "status": "SUCCESS",
                "finished_at": "2024-01-01T00:00:00.000000Z",
                "artifact_ref": "tasks/t1/x",
            }

        async def hset(self, *args, **kwargs):
            raise AssertionError("hset should not be called for terminal replay")

        async def aclose(self) -> None:
            return None

    fake = FakeClient()
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: fake)

    ok = await es.register_execution_start("req-terminal-1", "t1", "nmap", todo_id=None)
    assert ok is False


@pytest.mark.asyncio
async def test_register_execution_start_proceeds_when_redis_record_running(monkeypatch):
    import redis.asyncio as redis_asyncio

    es = _load_execution_store()

    class FakeClient:
        async def exists(self, key: str) -> int:
            return 1

        async def hgetall(self, key: str) -> dict:
            return {
                "task_id": "t1",
                "skill_id": "nmap",
                "status": "RUNNING",
                "started_at": "2024-01-01T00:00:00.000000Z",
                "artifact_ref": "",
            }

        async def hset(self, *args, **kwargs):
            raise AssertionError("should not create new record when orchestrator pre-registered")

        async def aclose(self) -> None:
            return None

    fake = FakeClient()
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: fake)

    ok = await es.register_execution_start("req-running-1", "t1", "nmap", todo_id=None)
    assert ok is True


@pytest.mark.asyncio
async def test_register_execution_start_creates_when_key_missing(monkeypatch):
    import redis.asyncio as redis_asyncio

    es = _load_execution_store()

    class FakeClient:
        def __init__(self) -> None:
            self.written: dict | None = None

        async def exists(self, key: str) -> int:
            return 0

        async def hset(self, key: str, mapping=None, **kwargs):
            self.written = dict(mapping or {})

        async def aclose(self) -> None:
            return None

    fake = FakeClient()
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: fake)

    ok = await es.register_execution_start("req-new-1", "t1", "http-enum", todo_id="todo-1")
    assert ok is True
    assert fake.written is not None
    assert fake.written.get("status") == "RUNNING"
    assert fake.written.get("task_id") == "t1"
    assert fake.written.get("todo_id") == "todo-1"
