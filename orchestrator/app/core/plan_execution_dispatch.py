"""
Plan 模式：将 `_latest_plan_list` 中未派发的项按序经 InstructionCompiler 编译后交给 ExecutionDispatcher。

r4f-c：派发前 `compile_plan_item`；失败不执行未编译 params，写入 `_latest_compile_trace` 并卡住该项待下 tick 重试。

r4f-b：生产代码中 **PlanItem → compile_plan_item** 的唯一入口；整体派发拓扑见 `docs/architecture.md`。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict

from app.models import ActionItem, TaskState, TraceEvent
from app.plan_models import PlanList, PlanItem
from app.core.correlation_ids import attach_correlation
from app.core.instruction_compiler import compile_plan_item
from app.core.plan_list_decision import LATEST_PLAN_LIST_CONTEXT_KEY
from app.structured_error_envelope import plan_error_envelope_to_client_dict

logger = logging.getLogger(__name__)

PLAN_DISPATCH_NEXT_INDEX_KEY = "_plan_dispatch_next_index"
PLAN_COMPILE_RETRY_COUNTS_KEY = "_plan_compile_retry_counts"

_SKILL_ID_ALIASES: dict[str, str] = {
    "web-fingerprint": "whatweb-fingerprint",
    "baidu_search": "baidu-search",
    "baidu search": "baidu-search",
}


def _compile_retry_max() -> int:
    raw = (os.getenv("ORCH_PLAN_COMPILE_MAX_RETRIES") or "3").strip()
    try:
        return max(1, min(int(raw), 20))
    except ValueError:
        return 3


def _compile_retry_key(plan_id: str, dispatch_index: int) -> str:
    return f"{dispatch_index}:{plan_id}"


def _resolve_skill_id_alias(skill_id: str | None, available_skills: list[str]) -> str:
    raw = (skill_id or "").strip()
    if not raw:
        return ""
    if raw in available_skills:
        return raw
    mapped = _SKILL_ID_ALIASES.get(raw.lower(), "")
    if mapped and mapped in available_skills:
        return mapped
    return raw


def _append_compile_trace(target_context: Dict[str, Any], entry: Dict[str, Any]) -> None:
    prev = target_context.get("_latest_compile_trace")
    if isinstance(prev, list):
        merged = list(prev) + [entry]
    elif prev is not None:
        merged = [prev, entry]
    else:
        merged = [entry]
    target_context["_latest_compile_trace"] = merged[-50:]


async def try_dispatch_pending_plan_list_item(
    *,
    state: TaskState,
    available_skills: list[str],
    enable_executor: bool,
    _emit,
    _ts,
    _run_actions_and_merge_results,
    current_todo: Any = None,
    task_store: Any = None,
) -> bool:
    """
    若存在未完成派发的 plan item，则编译并执行下一项，返回 True（本 tick 应结束）。
    无可派发项时返回 False，由调用方继续 Planner。
    """
    ctx = state.target_context if isinstance(state.target_context, dict) else None
    if not ctx:
        return False

    raw = ctx.get(LATEST_PLAN_LIST_CONTEXT_KEY)
    if not raw:
        return False

    try:
        plan_list = PlanList.model_validate(raw)
    except Exception as ex:
        logger.warning("plan_dispatch skip: invalid _latest_plan_list: %s", ex)
        return False

    items = plan_list.items or []
    n = len(items)
    if n == 0:
        return False

    idx = int(ctx.get(PLAN_DISPATCH_NEXT_INDEX_KEY) or 0)
    if idx < 0:
        idx = 0
    if idx >= n:
        return False

    item: PlanItem = items[idx]
    raw_skill = (item.skill_id or "").strip()
    resolved_skill = _resolve_skill_id_alias(raw_skill, available_skills)
    if not resolved_skill or resolved_skill not in available_skills:
        err_payload: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "plan_id": item.plan_id,
            "skill_id": raw_skill,
            "resolved_skill_id": resolved_skill,
            "dispatch_index": idx,
            "reason": "resolved_skill_not_available_for_dispatch",
        }
        attach_correlation(err_payload, state.task_id, plan_id=item.plan_id)
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="PLAN_DISPATCH_SKIPPED",
                source_module="orchestrator",
                payload=err_payload,
            )
        )
        ctx[PLAN_DISPATCH_NEXT_INDEX_KEY] = idx + 1
        state.updated_at = datetime.utcnow()
        return True

    compiled_item = item
    cres = compile_plan_item(compiled_item, execution_target=state.target, verify_chunks=None)
    if not cres.ok or cres.instruction is None:
        env = cres.error
        structured_error = plan_error_envelope_to_client_dict(env) if env is not None else None
        trace_entry = {
            "ok": False,
            "plan_id": compiled_item.plan_id,
            "skill_id": compiled_item.skill_id,
            "dispatch_index": idx,
            "error": structured_error,
        }
        _append_compile_trace(ctx, trace_entry)
        fail_payload: Dict[str, Any] = {
            "phase": state.current_phase.value,
            "plan_id": compiled_item.plan_id,
            "skill_id": compiled_item.skill_id,
            "dispatch_index": idx,
            "structured_error": trace_entry.get("error"),
        }
        attach_correlation(fail_payload, state.task_id, plan_id=compiled_item.plan_id)
        await _emit(
            TraceEvent(
                task_id=state.task_id,
                timestamp=_ts(),
                event_type="INSTRUCTION_COMPILE_FAILED",
                source_module="orchestrator",
                payload=fail_payload,
            )
        )
        details = (structured_error or {}).get("details") if isinstance(structured_error, dict) else {}
        bad_chunk_id = str((details or {}).get("chunk_id") or "").strip()
        suggested_action = str((details or {}).get("suggested_action") or "").strip().lower()
        if suggested_action == "retry_with_replan":
            # Immediately abort this plan batch and force a new LLM planning cycle.
            # Retrying the same compiled item that references a non-existent chunk will
            # always fail — looping on it creates a sub-second busy-spin.  Record the
            # bad chunk_id in history so the LLM knows not to hallucinate it again.
            retries = ctx.get(PLAN_COMPILE_RETRY_COUNTS_KEY)
            if not isinstance(retries, dict):
                retries = {}
            rkey = _compile_retry_key(str(compiled_item.plan_id), idx)
            retry_count = int(retries.get(rkey) or 0) + 1
            max_retries = _compile_retry_max()
            ctx.pop(LATEST_PLAN_LIST_CONTEXT_KEY, None)
            ctx.pop(PLAN_DISPATCH_NEXT_INDEX_KEY, None)
            ctx.pop(PLAN_COMPILE_RETRY_COUNTS_KEY, None)
            replan_payload: Dict[str, Any] = {
                "phase": state.current_phase.value,
                "plan_id": compiled_item.plan_id,
                "skill_id": compiled_item.skill_id,
                "dispatch_index": idx,
                "retry_count": retry_count,
                "max_retries": max_retries,
                "reason": "compile_failed_replan",
            }
            if bad_chunk_id:
                replan_payload["bad_chunk_id"] = bad_chunk_id
            attach_correlation(replan_payload, state.task_id, plan_id=compiled_item.plan_id)
            await _emit(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=_ts(),
                    event_type="PLAN_DISPATCH_REPLAN_TRIGGERED",
                    source_module="orchestrator",
                    payload=replan_payload,
                )
            )
            hint = "[COMPILE_FAILED] plan_id=" + str(compiled_item.plan_id) + " skill=" + str(compiled_item.skill_id)
            if bad_chunk_id:
                hint += f"; chunk_id {bad_chunk_id!r} does not exist — do NOT reference it in future plans"
            else:
                hint += "; fix chunk refs or scope and retry"
            hint += "; pending plan batch cleared, replanning now."
            state.history_summary = ((state.history_summary or "").strip() + "\n" + hint).strip()
            state.updated_at = datetime.utcnow()
            return False
        state.history_summary = (
            (state.history_summary or "").strip()
            + "\n[COMPILE_FAILED] plan_id="
            + str(compiled_item.plan_id)
            + " skill="
            + str(compiled_item.skill_id)
            + "; fix chunk refs or scope and retry."
        ).strip()
        state.updated_at = datetime.utcnow()
        return True

    ins = cres.instruction
    ok_entry = {
        "ok": True,
        "plan_id": ins.plan_id,
        "skill_id": ins.skill_id,
        "dispatch_index": idx,
        "target": str(ins.target),
        "timeout_seconds": ins.timeout_seconds,
    }
    _append_compile_trace(ctx, ok_entry)

    params = dict(ins.params or {})
    params["target"] = str(ins.target)
    params["timeout"] = int(ins.timeout_seconds)
    action = ActionItem(skill_id=str(ins.skill_id), params=params)
    await _run_actions_and_merge_results(
        state,
        [action],
        available_skills,
        enable_executor,
        current_todo=current_todo,
        task_store=task_store,
        plan_id_for_invocation=str(ins.plan_id),
    )

    ctx[PLAN_DISPATCH_NEXT_INDEX_KEY] = idx + 1
    ctx.pop(PLAN_COMPILE_RETRY_COUNTS_KEY, None)
    state.updated_at = datetime.utcnow()
    # 将更新后的 next_index 立即写入 workspace 文件，防止因 PAUSED/进程重启后加载旧快照
    # 时 _plan_dispatch_next_index 仍为旧值（apply_execution_result 在派发中途已写过一次快照）
    # 而导致同一 Plan 项被重复派发。
    try:
        from app.core.workspace_store import write_task_context
        write_task_context(state.task_id, state.target_context)
    except Exception:
        pass
    return True
