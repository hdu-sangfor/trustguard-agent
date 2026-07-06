"""
断点恢复时的阶段墙钟：从 evidence checkpoint 与 TaskStore 合并锚点。

TaskStore（如 Redis）在编排器未全量重启时常仍保留 phase_start_at；同阶段下优先于
checkpoint，避免仅靠 evidence GET 缺失字段时误用 utcnow() 导致计时归零。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.core.task_store import TaskRecord
from app.enums import Phase
from app.models import TaskState


def parse_phase_clock_from_checkpoint_payload(
    checkpoint: Dict[str, Any],
) -> tuple[Optional[datetime], Optional[int]]:
    ps_raw = checkpoint.get("phase_start_at")
    ps: Optional[datetime] = None
    if isinstance(ps_raw, str) and ps_raw.strip():
        try:
            ps = datetime.fromisoformat(ps_raw.strip().rstrip("Z"))
        except ValueError:
            ps = None
    lim_raw = checkpoint.get("current_phase_duration_limit_sec")
    lim: Optional[int] = None
    if isinstance(lim_raw, int):
        lim = max(0, lim_raw)
    elif isinstance(lim_raw, str) and lim_raw.strip().isdigit():
        lim = max(0, int(lim_raw.strip()))
    return ps, lim


def _hydrate_finops_from_checkpoint_fallback(
    state: TaskState,
    checkpoint: Dict[str, Any],
) -> None:
    """
    当 TaskStore（Redis）中 finops 全为零时（如 Redis 被清空），从 evidence checkpoint 回落还原。
    TaskStore 有有效累计时跳过，不覆盖已知数据。
    """
    if int(state.llm_input_tokens_total or 0) + int(state.llm_output_tokens_total or 0) > 0:
        return
    chk_inp = int(checkpoint.get("llm_input_tokens_total") or 0)
    chk_out = int(checkpoint.get("llm_output_tokens_total") or 0)
    chk_cost = float(checkpoint.get("cumulative_cost_usd") or 0.0)
    if chk_inp > 0 or chk_out > 0 or chk_cost > 0.0:
        state.llm_input_tokens_total = chk_inp
        state.llm_output_tokens_total = chk_out
        state.cumulative_cost_usd = chk_cost


def hydrate_phase_clock_from_checkpoint_and_store(
    state: TaskState,
    checkpoint: Dict[str, Any],
    *,
    phase: Phase,
    store_record: Optional[TaskRecord] = None,
) -> None:
    ps_cp, lim_cp = parse_phase_clock_from_checkpoint_payload(checkpoint)
    if (
        store_record is not None
        and store_record.phase_start_at is not None
        and store_record.current_phase == phase
    ):
        lim = store_record.current_phase_duration_limit_sec
        if lim is None:
            lim = lim_cp
        state.hydrate_phase_clock(
            current_phase=phase,
            phase_start_at=store_record.phase_start_at,
            current_phase_duration_limit_sec=lim,
        )
        state.hydrate_finops_counters(store_record)
        # TaskStore finops 为零时（如本次续跑首次计费前）尝试从 checkpoint 补回
        _hydrate_finops_from_checkpoint_fallback(state, checkpoint)
        return
    anchor = ps_cp if ps_cp is not None else datetime.utcnow()
    state.hydrate_phase_clock(
        current_phase=phase,
        phase_start_at=anchor,
        current_phase_duration_limit_sec=lim_cp,
    )
    if store_record is not None:
        state.hydrate_finops_counters(store_record)
        # 同样尝试 checkpoint 兜底
        _hydrate_finops_from_checkpoint_fallback(state, checkpoint)
    else:
        # store_record 为 None（Redis 完全清空）：直接从 checkpoint 还原
        _hydrate_finops_from_checkpoint_fallback(state, checkpoint)
