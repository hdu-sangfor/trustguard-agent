"""r4f-d：Plan 派发路径上编译失败时不执行未编译 params，Trace 可观测且与 r4d suggested_action 对齐。"""
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.models import TaskState  # type: ignore[import]
from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem, PlanList  # type: ignore[import]
from app.core.plan_execution_dispatch import (  # type: ignore[import]
    PLAN_DISPATCH_NEXT_INDEX_KEY,
    try_dispatch_pending_plan_list_item,
)
from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY  # type: ignore[import]


@pytest.fixture
def chunk_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _failing_item_missing_chunk(task_id: str) -> PlanItem:
    missing = "chk-" + "f" * 32
    return PlanItem(
        plan_id="p-r4fd",
        task_id=task_id,
        skill_id="nmap",
        plan_content="scan",
        context_chunk_refs=[ContextChunkRef(chunk_id=missing, tenant_id=None)],
        constraints=PlanConstraints(
            target_scope="host:example.com",
            timeout_seconds=120,
            max_parallelism=1,
        ),
        metadata={},
    )


def _failing_item_with_sensitive_text(task_id: str) -> PlanItem:
    it = _failing_item_missing_chunk(task_id)
    it.plan_content = "do not leak: API_KEY=SECRET-R4FD-12345"
    return it


@pytest.mark.asyncio
async def test_r4f_d_compile_fail_does_not_call_run_actions(
    chunk_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compile fail with retry_with_replan immediately clears the queue and returns False (no busy-spin)."""
    state = TaskState("t-r4fd", "n", "https://example.com/")
    item = _failing_item_missing_chunk(state.task_id)
    plan_list = PlanList(task_id=state.task_id, items=[item])
    state.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = plan_list.model_dump(mode="json")
    state.target_context[PLAN_DISPATCH_NEXT_INDEX_KEY] = 0

    run_actions = AsyncMock()
    emit = AsyncMock()

    def _ts() -> str:
        return datetime.utcnow().isoformat() + "Z"

    out = await try_dispatch_pending_plan_list_item(
        state=state,
        available_skills=["nmap"],
        enable_executor=True,
        _emit=emit,
        _ts=_ts,
        _run_actions_and_merge_results=run_actions,
        current_todo=None,
        task_store=None,
    )
    # New behavior: immediately trigger replan (return False) instead of retrying same item
    assert out is False
    run_actions.assert_not_awaited()
    # Plan queue must be cleared so the state machine calls the LLM for a fresh plan
    assert LATEST_PLAN_LIST_CONTEXT_KEY not in state.target_context
    assert PLAN_DISPATCH_NEXT_INDEX_KEY not in state.target_context


@pytest.mark.asyncio
async def test_r4f_d_compile_fail_emits_trace_with_r4d_suggested_action(
    chunk_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = TaskState("t-r4fd2", "n", "https://example.com/")
    item = _failing_item_missing_chunk(state.task_id)
    plan_list = PlanList(task_id=state.task_id, items=[item])
    state.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = plan_list.model_dump(mode="json")
    state.target_context[PLAN_DISPATCH_NEXT_INDEX_KEY] = 0

    emitted: list = []

    async def _emit(ev) -> None:
        emitted.append(ev)

    def _ts() -> str:
        return datetime.utcnow().isoformat() + "Z"

    await try_dispatch_pending_plan_list_item(
        state=state,
        available_skills=["nmap"],
        enable_executor=True,
        _emit=_emit,
        _ts=_ts,
        _run_actions_and_merge_results=AsyncMock(),
        current_todo=None,
        task_store=None,
    )

    fail_events = [e for e in emitted if getattr(e, "event_type", None) == "INSTRUCTION_COMPILE_FAILED"]
    assert len(fail_events) == 1
    payload = fail_events[0].payload
    assert payload.get("plan_id") == "p-r4fd"
    assert payload.get("skill_id") == "nmap"
    se = payload.get("structured_error") or {}
    assert se.get("details", {}).get("suggested_action") == "retry_with_replan"
    assert se.get("details", {}).get("chunk_id") == item.context_chunk_refs[0].chunk_id

    trace = state.target_context.get("_latest_compile_trace")
    assert isinstance(trace, list) and len(trace) >= 1
    assert trace[-1].get("ok") is False
    assert trace[-1].get("error", {}).get("details", {}).get("suggested_action") == "retry_with_replan"


@pytest.mark.asyncio
async def test_r4f_d_compile_fail_payload_is_sanitized_no_secret_body(
    chunk_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = TaskState("t-r4fd3", "n", "https://example.com/")
    item = _failing_item_with_sensitive_text(state.task_id)
    plan_list = PlanList(task_id=state.task_id, items=[item])
    state.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = plan_list.model_dump(mode="json")
    state.target_context[PLAN_DISPATCH_NEXT_INDEX_KEY] = 0

    emitted: list = []

    async def _emit(ev) -> None:
        emitted.append(ev)

    def _ts() -> str:
        return datetime.utcnow().isoformat() + "Z"

    await try_dispatch_pending_plan_list_item(
        state=state,
        available_skills=["nmap"],
        enable_executor=True,
        _emit=_emit,
        _ts=_ts,
        _run_actions_and_merge_results=AsyncMock(),
        current_todo=None,
        task_store=None,
    )

    fail_events = [e for e in emitted if getattr(e, "event_type", None) == "INSTRUCTION_COMPILE_FAILED"]
    assert len(fail_events) == 1
    payload_s = str(fail_events[0].payload)
    trace_s = str(state.target_context.get("_latest_compile_trace"))
    history_s = str(state.history_summary or "")
    assert "SECRET-R4FD-12345" not in payload_s
    assert "SECRET-R4FD-12345" not in trace_s
    assert "SECRET-R4FD-12345" not in history_s


@pytest.mark.asyncio
async def test_r4f_d_compile_retry_exhausted_triggers_replan_clear_queue(
    chunk_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCH_PLAN_COMPILE_MAX_RETRIES", "1")
    state = TaskState("t-r4fd4", "n", "https://example.com/")
    item = _failing_item_missing_chunk(state.task_id)
    plan_list = PlanList(task_id=state.task_id, items=[item])
    state.target_context[LATEST_PLAN_LIST_CONTEXT_KEY] = plan_list.model_dump(mode="json")
    state.target_context[PLAN_DISPATCH_NEXT_INDEX_KEY] = 0

    emitted: list = []

    async def _emit(ev) -> None:
        emitted.append(ev)

    out = await try_dispatch_pending_plan_list_item(
        state=state,
        available_skills=["nmap"],
        enable_executor=True,
        _emit=_emit,
        _ts=lambda: datetime.utcnow().isoformat() + "Z",
        _run_actions_and_merge_results=AsyncMock(),
        current_todo=None,
        task_store=None,
    )
    assert out is False
    assert LATEST_PLAN_LIST_CONTEXT_KEY not in state.target_context
    assert PLAN_DISPATCH_NEXT_INDEX_KEY not in state.target_context
    assert any(getattr(e, "event_type", None) == "PLAN_DISPATCH_REPLAN_TRIGGERED" for e in emitted)
