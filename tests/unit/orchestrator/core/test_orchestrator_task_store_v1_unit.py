"""编排器 TaskStore：`artifact_refs_v1` 读写字段与 Pydantic JSON 校验（无真实 Redis）。"""
from __future__ import annotations

import contextlib
from typing import Any, Callable, Generator, Tuple, Type

import pytest

from tests.orchestrator_test_env import orchestrator_sys_path_isolated


@contextlib.contextmanager
def _orch_task_store_context() -> Generator[
    Tuple[Type[Any], Callable[..., Any]],
    None,
    None,
]:
    with orchestrator_sys_path_isolated():
        from app.core.task_store import InMemoryTaskStore, parse_artifact_refs_v1_from_redis  # noqa: E402

        yield InMemoryTaskStore, parse_artifact_refs_v1_from_redis


def test_parse_artifact_refs_v1_valid() -> None:
    with _orch_task_store_context() as (_, parse_fn):
        assert parse_fn('["wsref:a", "wsref:b"]') == ["wsref:a", "wsref:b"]
        assert parse_fn("[]") == []


def test_parse_artifact_refs_v1_empty_or_bad() -> None:
    with _orch_task_store_context() as (_, parse_fn):
        assert parse_fn(None) is None
        assert parse_fn("") is None
        assert parse_fn("not-json") is None
        assert parse_fn("{}") is None
        assert parse_fn('["a", 1]') is None


@pytest.mark.asyncio
async def test_inmemory_register_finish_roundtrip_v1() -> None:
    with _orch_task_store_context() as (InMemoryTaskStore, _):
        store = InMemoryTaskStore()
        await store.register_execution_start("req-v1", "t1", "skill-x", todo_id=None)
        await store.register_execution_finish(
            "req-v1",
            "t1",
            "DONE",
            artifact_ref="wsref:primary",
            artifact_refs_v1=["wsref:primary", "wsref:secondary"],
        )
        rec = await store.get_execution_status("req-v1")
        assert rec is not None
        assert rec.artifact_ref == "wsref:primary"
        assert rec.artifact_refs_v1 == ["wsref:primary", "wsref:secondary"]


@pytest.mark.asyncio
async def test_inmemory_finish_omitting_v1_does_not_clear_prior() -> None:
    with _orch_task_store_context() as (InMemoryTaskStore, _):
        store = InMemoryTaskStore()
        await store.register_execution_start("req-v1b", "t1", "skill-x", todo_id=None)
        await store.register_execution_finish(
            "req-v1b",
            "t1",
            "DONE",
            artifact_ref="a",
            artifact_refs_v1=["r1", "r2"],
        )
        await store.register_execution_finish(
            "req-v1b",
            "t1",
            "DONE",
            artifact_ref="b",
        )
        rec = await store.get_execution_status("req-v1b")
        assert rec is not None
        assert rec.artifact_ref == "b"
        assert rec.artifact_refs_v1 == ["r1", "r2"]
