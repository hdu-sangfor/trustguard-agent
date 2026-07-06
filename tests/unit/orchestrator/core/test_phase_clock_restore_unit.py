"""phase_clock_restore：checkpoint 与 TaskStore 合并阶段墙钟锚点。"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.orchestrator_test_env import orchestrator_sys_path_isolated


@pytest.fixture
def _env():
    with orchestrator_sys_path_isolated():
        yield


def test_parse_phase_clock_from_checkpoint_payload(_env: None) -> None:
    from app.core.phase_clock_restore import parse_phase_clock_from_checkpoint_payload

    ps, lim = parse_phase_clock_from_checkpoint_payload(
        {"phase_start_at": "2021-06-15T10:20:30Z", "current_phase_duration_limit_sec": 99}
    )
    assert ps == datetime(2021, 6, 15, 10, 20, 30)
    assert lim == 99


def test_hydrate_prefers_task_store_when_same_phase(_env: None) -> None:
    from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store
    from app.core.task_store import TaskRecord
    from app.models import Phase, TaskState, TaskStatus

    store_anchor = datetime(2019, 1, 1, 0, 0, 0)
    rec = TaskRecord(
        task_id="t1",
        name="n",
        target="x",
        status=TaskStatus.RUNNING,
        current_phase=Phase.RECON,
        phase_start_at=store_anchor,
        current_phase_duration_limit_sec=55,
    )
    state = TaskState(task_id="t1", name="n", target="x")
    checkpoint = {
        "phase_start_at": "2025-01-01T00:00:00Z",
        "current_phase_duration_limit_sec": 120,
    }
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=Phase.RECON, store_record=rec
    )
    assert state.phase_start_at == store_anchor
    assert state.current_phase_duration_limit_sec == 55


def test_hydrate_falls_back_to_checkpoint_when_store_phase_differs(_env: None) -> None:
    from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store
    from app.core.task_store import TaskRecord
    from app.models import Phase, TaskState, TaskStatus

    rec = TaskRecord(
        task_id="t1",
        name="n",
        target="x",
        status=TaskStatus.RUNNING,
        current_phase=Phase.EXPLOIT,
        phase_start_at=datetime(2019, 1, 1, 0, 0, 0),
        current_phase_duration_limit_sec=55,
    )
    state = TaskState(task_id="t1", name="n", target="x")
    checkpoint = {
        "phase_start_at": "2025-03-03T12:00:00Z",
        "current_phase_duration_limit_sec": 88,
    }
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=Phase.RECON, store_record=rec
    )
    assert state.phase_start_at == datetime(2025, 3, 3, 12, 0, 0)
    assert state.current_phase_duration_limit_sec == 88


def test_hydrate_store_limit_none_uses_checkpoint_limit(_env: None) -> None:
    from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store
    from app.core.task_store import TaskRecord
    from app.models import Phase, TaskState, TaskStatus

    rec = TaskRecord(
        task_id="t1",
        name="n",
        target="x",
        status=TaskStatus.RUNNING,
        current_phase=Phase.RECON,
        phase_start_at=datetime(2019, 1, 1, 0, 0, 0),
        current_phase_duration_limit_sec=None,
    )
    state = TaskState(task_id="t1", name="n", target="x")
    checkpoint = {"current_phase_duration_limit_sec": 77}
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=Phase.RECON, store_record=rec
    )
    assert state.phase_start_at == datetime(2019, 1, 1, 0, 0, 0)
    assert state.current_phase_duration_limit_sec == 77
