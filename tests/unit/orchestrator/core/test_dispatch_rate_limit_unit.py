"""R6d：派发层 target / workspace 在途并发槽。"""
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH_ROOT = REPO_ROOT / "orchestrator"
if str(_ORCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_ORCH_ROOT))

import app.core.dispatch_rate_limit as drl  # type: ignore[import]
from app.core.task_store import InMemoryTaskStore  # type: ignore[import]


@pytest.mark.asyncio
async def test_inmemory_rate_slot_respects_limit():
    store = InMemoryTaskStore()
    assert await store.try_acquire_rate_slot("t_abc", 1) is True
    assert await store.try_acquire_rate_slot("t_abc", 1) is False
    await store.release_rate_slot("t_abc")
    assert await store.try_acquire_rate_slot("t_abc", 1) is True


def test_rate_limit_target_scope_key_stable():
    a = drl.rate_limit_target_scope_key("http://x/ ")
    b = drl.rate_limit_target_scope_key("http://x/ ")
    assert a == b and a.startswith("t_")


def test_rate_limit_workspace_scope_key():
    assert drl.rate_limit_workspace_scope_key({}) == ""
    assert drl.rate_limit_workspace_scope_key({"workspace_id": "acme"}) == "w_acme"
    assert drl.rate_limit_workspace_scope_key({"project_id": "p1"}) == "p_p1"


@pytest.mark.asyncio
async def test_acquire_second_identical_target_workspace_blocked(monkeypatch):
    monkeypatch.setattr(drl, "dispatch_max_inflight_per_target", lambda: 1)
    monkeypatch.setattr(drl, "dispatch_max_inflight_per_workspace", lambda: 1)
    store = InMemoryTaskStore()
    ok1, r1 = await drl.acquire_dispatch_rate_limits(
        store,
        target="http://example.com/z",
        context={"workspace_id": "tenant-1"},
    )
    assert ok1 and len(r1) == 2
    ok2, r2 = await drl.acquire_dispatch_rate_limits(
        store,
        target="http://example.com/z",
        context={"workspace_id": "tenant-1"},
    )
    assert ok2 is False and r2 == []
    await drl.release_dispatch_rate_limits(r1)


@pytest.mark.asyncio
async def test_workspace_fail_releases_target_slot(monkeypatch):
    """先占满 workspace，再换 target 尝试：应失败且不占新 target 槽。"""
    monkeypatch.setattr(drl, "dispatch_max_inflight_per_target", lambda: 4)
    monkeypatch.setattr(drl, "dispatch_max_inflight_per_workspace", lambda: 1)
    store = InMemoryTaskStore()
    ok1, r1 = await drl.acquire_dispatch_rate_limits(
        store,
        target="http://a",
        context={"workspace_id": "shared"},
    )
    assert ok1
    tk_b = drl.rate_limit_target_scope_key("http://b")
    ok2, r2 = await drl.acquire_dispatch_rate_limits(
        store,
        target="http://b",
        context={"workspace_id": "shared"},
    )
    assert ok2 is False and r2 == []
    assert int(store._rate_slots.get(tk_b) or 0) == 0
    await drl.release_dispatch_rate_limits(r1)
