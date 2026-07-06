"""
调用Evidence保存/读取任务断点（停止时保存，续跑时恢复）。
"""
import logging
import os
from typing import Any, Dict, Optional

import httpx

from app.clients.evidence_http_error import evidence_http_error_detail

EVIDENCE_BASE_URL = os.getenv("EVIDENCE_BASE_URL", "http://localhost:18103")

_log = logging.getLogger(__name__)


def checkpoint_include_phase_clock_fields() -> bool:
    """
    是否在Evidence checkpoint PUT 的 JSON 中包含 phase_start_at / current_phase_duration_limit_sec。
    当前 evidence 已持久化两字段；设为 0/false/off/no 时省略键，evidence 会保留库内已有相位时钟列不覆盖。
    编排器 restore/resume 仍会在同阶段下优先采用 TaskStore（如 Redis）中的锚点。
    """
    raw = (os.getenv("ORCH_CHECKPOINT_INCLUDE_PHASE_CLOCK") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


async def save_checkpoint(
    task_id: str,
    current_phase: str,
    status: str,
    target_context: Dict[str, Any],
    history_summary: str,
    name: Optional[str] = None,
    target: Optional[str] = None,
    description: Optional[str] = None,
    *,
    phase_start_at: Optional[str] = None,
    current_phase_duration_limit_sec: Optional[int] = None,
    llm_input_tokens_total: Optional[int] = None,
    llm_output_tokens_total: Optional[int] = None,
    cumulative_cost_usd: Optional[float] = None,
) -> None:
    payload: Dict[str, Any] = {
        "current_phase": current_phase,
        "status": status,
        "target_context": target_context,
        "history_summary": history_summary or "",
        "name": name,
        "target": target,
        "description": description,
    }
    if checkpoint_include_phase_clock_fields():
        payload["phase_start_at"] = phase_start_at
        payload["current_phase_duration_limit_sec"] = current_phase_duration_limit_sec
    # FinOps 断点持久化：仅在有实际累计时写入，避免零值覆盖库内已有记录
    _inp = int(llm_input_tokens_total or 0)
    _outp = int(llm_output_tokens_total or 0)
    _cost = float(cumulative_cost_usd or 0.0)
    if _inp > 0 or _outp > 0 or _cost > 0.0:
        payload["llm_input_tokens_total"] = _inp
        payload["llm_output_tokens_total"] = _outp
        payload["cumulative_cost_usd"] = _cost
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{EVIDENCE_BASE_URL}/internal/tasks/{task_id}/checkpoint",
            json=payload,
            timeout=10.0,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            _log.warning(
                "evidence save_checkpoint task_id=%s %s",
                task_id,
                evidence_http_error_detail(e.response),
            )
            raise


async def load_checkpoint(task_id: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{EVIDENCE_BASE_URL}/internal/tasks/{task_id}/checkpoint",
            timeout=10.0,
        )
        if resp.status_code == 404 or resp.status_code == 204:
            return None
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            _log.warning(
                "evidence load_checkpoint task_id=%s %s",
                task_id,
                evidence_http_error_detail(e.response),
            )
            raise
        data = resp.json()
        return data if data else None
