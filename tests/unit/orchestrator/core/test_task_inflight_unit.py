"""TaskStore 单任务在途计数（MAX_IN_FLIGHT_SKILLS_PER_TASK）单元测试。"""
import asyncio
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _orch_path():
    return str(REPO_ROOT / "orchestrator")


def test_inmemory_try_acquire_release_respects_limit():
    sys.path.insert(0, _orch_path())
    try:
        from app.core.task_store import InMemoryTaskStore

        async def _run():
            s = InMemoryTaskStore()
            assert await s.try_acquire_task_inflight("task-a", 2) is True
            assert await s.try_acquire_task_inflight("task-a", 2) is True
            assert await s.try_acquire_task_inflight("task-a", 2) is False
            await s.release_task_inflight("task-a")
            assert await s.try_acquire_task_inflight("task-a", 2) is True
            await s.release_task_inflight("task-a")
            await s.release_task_inflight("task-a")

        asyncio.run(_run())
    finally:
        sys.path.remove(_orch_path())


def test_inmemory_zero_limit_means_unlimited_gate():
    sys.path.insert(0, _orch_path())
    try:
        from app.core.task_store import InMemoryTaskStore

        async def _run():
            s = InMemoryTaskStore()
            for _ in range(20):
                assert await s.try_acquire_task_inflight("t2", 0) is True

        asyncio.run(_run())
    finally:
        sys.path.remove(_orch_path())
