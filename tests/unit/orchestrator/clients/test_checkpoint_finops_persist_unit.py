"""
FinOps 断点持久化单元测试。

覆盖：
1. save_checkpoint 在 state 有 finops 时将三字段写入 HTTP payload
2. save_checkpoint 在 finops 全零时不写入（避免零值覆盖库内记录）
3. save_checkpoint 未传 finops 参数时不写入
4. phase_clock_restore: store_record finops 为零、checkpoint 有数据时正确回落
5. phase_clock_restore: store_record 为 None 时直接从 checkpoint 还原
6. phase_clock_restore: store_record finops > 0 时 checkpoint 不覆盖
7. phase_clock_restore: store_record=None 且 checkpoint 无 finops 时 state 保持零
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.orchestrator_test_env import orchestrator_sys_path_isolated


@pytest.fixture
def _env():
    with orchestrator_sys_path_isolated():
        yield


# ---------------------------------------------------------------------------
# Shared fake httpx client（与 test_checkpoint_client_unit.py 风格一致）
# ---------------------------------------------------------------------------

class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.captured: dict = {}

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def put(self, url: str, json=None, timeout=None):
        self.captured["url"] = url
        self.captured["json"] = json
        return _FakeResponse()


# ---------------------------------------------------------------------------
# checkpoint_client.save_checkpoint — payload 写入验证
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_checkpoint_includes_finops_when_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """finops > 0 时，payload 应包含三个 finops 键。"""
    monkeypatch.delenv("ORCH_CHECKPOINT_INCLUDE_PHASE_CLOCK", raising=False)
    fake = _FakeAsyncClient()
    with orchestrator_sys_path_isolated():
        with patch("app.clients.checkpoint_client.httpx.AsyncClient", return_value=fake):
            from app.clients.checkpoint_client import save_checkpoint

            await save_checkpoint(
                "t-finops1",
                "RECON",
                "RUNNING",
                {},
                "hist",
                llm_input_tokens_total=1000,
                llm_output_tokens_total=500,
                cumulative_cost_usd=0.025,
            )

    body = fake.captured["json"]
    assert body["llm_input_tokens_total"] == 1000
    assert body["llm_output_tokens_total"] == 500
    assert abs(body["cumulative_cost_usd"] - 0.025) < 1e-9


@pytest.mark.asyncio
async def test_save_checkpoint_omits_finops_when_all_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """finops 全零时，payload 不应包含 finops 键（避免零值覆盖库内已有记录）。"""
    monkeypatch.delenv("ORCH_CHECKPOINT_INCLUDE_PHASE_CLOCK", raising=False)
    fake = _FakeAsyncClient()
    with orchestrator_sys_path_isolated():
        with patch("app.clients.checkpoint_client.httpx.AsyncClient", return_value=fake):
            from app.clients.checkpoint_client import save_checkpoint

            await save_checkpoint(
                "t-finops2",
                "RECON",
                "RUNNING",
                {},
                "hist",
                llm_input_tokens_total=0,
                llm_output_tokens_total=0,
                cumulative_cost_usd=0.0,
            )

    body = fake.captured["json"]
    assert "llm_input_tokens_total" not in body
    assert "llm_output_tokens_total" not in body
    assert "cumulative_cost_usd" not in body


@pytest.mark.asyncio
async def test_save_checkpoint_omits_finops_when_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """未传 finops 参数时，payload 不包含 finops 键。"""
    monkeypatch.delenv("ORCH_CHECKPOINT_INCLUDE_PHASE_CLOCK", raising=False)
    fake = _FakeAsyncClient()
    with orchestrator_sys_path_isolated():
        with patch("app.clients.checkpoint_client.httpx.AsyncClient", return_value=fake):
            from app.clients.checkpoint_client import save_checkpoint

            await save_checkpoint(
                "t-finops3",
                "RECON",
                "RUNNING",
                {},
                "hist",
            )

    body = fake.captured["json"]
    assert "llm_input_tokens_total" not in body
    assert "llm_output_tokens_total" not in body
    assert "cumulative_cost_usd" not in body


# ---------------------------------------------------------------------------
# phase_clock_restore._hydrate_finops_from_checkpoint_fallback
# ---------------------------------------------------------------------------

def test_finops_fallback_applied_when_store_record_none(_env: None) -> None:
    """store_record=None（Redis 清空），应从 checkpoint 还原 finops。"""
    from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store
    from app.models import Phase, TaskState

    state = TaskState(task_id="t1", name="n", target="x")
    checkpoint = {
        "phase_start_at": "2024-01-01T00:00:00Z",
        "current_phase_duration_limit_sec": 60,
        "llm_input_tokens_total": 2000,
        "llm_output_tokens_total": 800,
        "cumulative_cost_usd": 0.05,
    }
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=Phase.RECON, store_record=None
    )
    assert state.llm_input_tokens_total == 2000
    assert state.llm_output_tokens_total == 800
    assert abs(state.cumulative_cost_usd - 0.05) < 1e-9


def test_finops_fallback_applied_when_store_record_finops_zero(_env: None) -> None:
    """store_record 存在但 finops 全零（续跑首次），应从 checkpoint 回落。"""
    from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store
    from app.core.task_store import TaskRecord
    from app.models import Phase, TaskState, TaskStatus
    from datetime import datetime

    rec = TaskRecord(
        task_id="t1",
        name="n",
        target="x",
        status=TaskStatus.RUNNING,
        current_phase=Phase.RECON,
        phase_start_at=datetime(2024, 1, 1),
        llm_input_tokens_total=0,
        llm_output_tokens_total=0,
        cumulative_cost_usd=0.0,
    )
    state = TaskState(task_id="t1", name="n", target="x")
    checkpoint = {
        "phase_start_at": "2024-01-01T00:00:00Z",
        "llm_input_tokens_total": 1500,
        "llm_output_tokens_total": 600,
        "cumulative_cost_usd": 0.03,
    }
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=Phase.RECON, store_record=rec
    )
    assert state.llm_input_tokens_total == 1500
    assert state.llm_output_tokens_total == 600
    assert abs(state.cumulative_cost_usd - 0.03) < 1e-9


def test_finops_fallback_not_applied_when_store_record_has_data(_env: None) -> None:
    """store_record 已有 finops 数据，checkpoint 不应覆盖。"""
    from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store
    from app.core.task_store import TaskRecord
    from app.models import Phase, TaskState, TaskStatus
    from datetime import datetime

    rec = TaskRecord(
        task_id="t1",
        name="n",
        target="x",
        status=TaskStatus.RUNNING,
        current_phase=Phase.RECON,
        phase_start_at=datetime(2024, 1, 1),
        llm_input_tokens_total=9999,
        llm_output_tokens_total=4444,
        cumulative_cost_usd=0.99,
    )
    state = TaskState(task_id="t1", name="n", target="x")
    checkpoint = {
        "phase_start_at": "2024-01-01T00:00:00Z",
        "llm_input_tokens_total": 1,
        "llm_output_tokens_total": 1,
        "cumulative_cost_usd": 0.001,
    }
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=Phase.RECON, store_record=rec
    )
    # TaskStore 优先，checkpoint 不应覆盖
    assert state.llm_input_tokens_total == 9999
    assert state.llm_output_tokens_total == 4444
    assert abs(state.cumulative_cost_usd - 0.99) < 1e-9


def test_finops_fallback_no_op_when_checkpoint_also_zero(_env: None) -> None:
    """store_record=None，checkpoint 也无 finops 数据，state 保持零。"""
    from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store
    from app.models import Phase, TaskState

    state = TaskState(task_id="t1", name="n", target="x")
    checkpoint = {"phase_start_at": "2024-01-01T00:00:00Z"}
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=Phase.RECON, store_record=None
    )
    assert state.llm_input_tokens_total == 0
    assert state.llm_output_tokens_total == 0
    assert state.cumulative_cost_usd == 0.0
