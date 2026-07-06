"""R2b：Plan 模式决策落盘与 PLAN_LIST_DECISION trace（无真实 LLM）。"""

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH_ROOT = REPO_ROOT / "orchestrator"


@pytest.mark.asyncio
async def test_handle_plan_list_decision_persists_context_and_emits_trace(monkeypatch):
    sys.path.insert(0, str(_ORCH_ROOT))
    try:
        from app.core import state_machine as sm
        from app.core.plan_execution_dispatch import PLAN_DISPATCH_NEXT_INDEX_KEY
        from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
        from app.models import TaskState
        from app.plan_models import PlanConstraints, PlanItem, PlanList
    finally:
        if str(_ORCH_ROOT) in sys.path:
            sys.path.remove(str(_ORCH_ROOT))

    captured: list = []

    async def capture_emit(ev):
        captured.append(ev)

    monkeypatch.setattr(sm, "_emit", capture_emit)

    state = TaskState(task_id="t-plan", name="n", target="http://example.com")
    plan = PlanList(
        task_id="t-plan",
        batch_id="batch-1",
        items=[
            PlanItem(
                plan_id="p1",
                task_id="t-plan",
                skill_id="nmap",
                plan_content="port scan",
                context_chunk_refs=[],
                constraints=PlanConstraints(target_scope="host:example.com", timeout_seconds=120),
            )
        ],
    )
    await sm._handle_plan_list_decision_path(state, plan, todo_id=None)

    assert LATEST_PLAN_LIST_CONTEXT_KEY in state.target_context
    dumped = state.target_context[LATEST_PLAN_LIST_CONTEXT_KEY]
    assert dumped["task_id"] == "t-plan"
    assert len(dumped.get("items") or []) == 1
    assert "[PLAN_LIST]" in (state.history_summary or "")
    assert state.target_context.get(PLAN_DISPATCH_NEXT_INDEX_KEY) == 0
    assert any(e.event_type == "PLAN_LIST_DECISION" for e in captured)
    payload = next(e for e in captured if e.event_type == "PLAN_LIST_DECISION").payload
    assert payload.get("item_count") == 1
    assert payload.get("skill_ids") == ["nmap"]


@pytest.mark.asyncio
async def test_handle_plan_list_includes_todo_id_in_payload(monkeypatch):
    sys.path.insert(0, str(_ORCH_ROOT))
    try:
        from app.core import state_machine as sm
        from app.models import TaskState
        from app.plan_models import PlanConstraints, PlanItem, PlanList
    finally:
        if str(_ORCH_ROOT) in sys.path:
            sys.path.remove(str(_ORCH_ROOT))

    captured: list = []

    async def capture_emit(ev):
        captured.append(ev)

    monkeypatch.setattr(sm, "_emit", capture_emit)

    state = TaskState(task_id="t2", name="n", target="http://x.com")
    plan = PlanList(
        task_id="t2",
        items=[
            PlanItem(
                plan_id="p1",
                task_id="t2",
                skill_id="curl-raw",
                plan_content="probe",
                context_chunk_refs=[],
                constraints=PlanConstraints(target_scope="host:x.com", timeout_seconds=30),
            )
        ],
    )
    await sm._handle_plan_list_decision_path(state, plan, todo_id="todo-99")
    payload = next(e for e in captured if e.event_type == "PLAN_LIST_DECISION").payload
    assert payload.get("todo_id") == "todo-99"
