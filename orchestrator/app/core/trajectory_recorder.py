from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import os

from app.core.agent_execution import AgentExecution, AgentState, AgentStep, AgentStepState
from app.core.workspace_store import WORKSPACE_ROOT, _safe_name  # type: ignore[attr-defined]


def _task_dir(task_id: str) -> Path:
    """与 workspace_store 中 task 目录保持一致的路径规则。"""
    return WORKSPACE_ROOT / _safe_name(task_id)


@dataclass
class ExecutionSnapshot:
    """用于落盘的轻量轨迹快照结构。"""

    task_id: str
    created_at: str
    phase: str | None
    agent_state: str
    final_result: str | None
    error: str | None
    step: dict[str, Any]


def _serialize_step(step: AgentStep) -> dict[str, Any]:
    """将 AgentStep 转为可 JSON 序列化的 dict。"""
    return {
        "step_number": step.step_number,
        "state": step.state.value,
        "phase": step.phase,
        "decision_raw": step.decision_raw,
        "skill_id": step.skill_id,
        "skill_target": step.skill_target,
        "skill_params": step.skill_params,
        "executor_status": step.executor_status,
        "executor_duration_ms": step.executor_duration_ms,
        "executor_artifacts": step.executor_artifacts,
        "executor_artifact_dir": step.executor_artifact_dir,
        "history_summary_before": step.history_summary_before,
        "history_summary_after": step.history_summary_after,
        "error": step.error,
        "extra": step.extra,
    }


def record_execution(
    execution: AgentExecution,
    *,
    phase: str | None,
) -> None:
    """
    将一次 AgentExecution 以独立 JSON 文件形式落盘。

    设计原则：
    - 与 workspace_store 的目录结构对齐：`workspace/{task_id}/agent_trajectories/*.json`；
    - 仅用于开发调试与后续报告，不影响核心 TraceEvent/DB 事实来源；
    - 若写入失败，静默忽略，不影响主流程。
    """
    try:
        task_id = execution.task_id
        now = datetime.utcnow().isoformat() + "Z"
        step = execution.steps[-1] if execution.steps else None
        snapshot = ExecutionSnapshot(
            task_id=task_id,
            created_at=now,
            phase=phase,
            agent_state=execution.final_state.value,
            final_result=execution.final_result,
            error=execution.error,
            step=_serialize_step(step) if step else {},
        )

        base_dir = _task_dir(task_id) / "agent_trajectories"
        base_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        phase_safe = (phase or "UNKNOWN").upper()
        path = base_dir / f"{ts}_{phase_safe}.json"
        path.write_text(
            json.dumps(asdict(snapshot), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        # 轨迹记录失败不应影响主流程
        return

