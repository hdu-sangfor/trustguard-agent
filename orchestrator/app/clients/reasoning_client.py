"""向 Evidence 写入结构化 Reasoning Step（与 emit_trace 并行）。"""

from __future__ import annotations

import logging
import os

import httpx

from app.clients.evidence_http_error import evidence_http_error_detail
from app.models import ReasoningStep

EVIDENCE_BASE_URL = os.getenv("EVIDENCE_BASE_URL", "http://localhost:18103")

_log = logging.getLogger(__name__)


async def emit_reasoning_step(step: ReasoningStep) -> None:
    """POST /v1/reasoning-steps；失败静默（MVP），打 warning 便于排障。"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{EVIDENCE_BASE_URL}/v1/reasoning-steps",
                json=step.model_dump(by_alias=False),
                timeout=5.0,
            )
            if resp.status_code >= 400:
                _log.warning(
                    "evidence emit_reasoning_step task_id=%s step_type=%s %s",
                    step.task_id,
                    step.step_type,
                    evidence_http_error_detail(resp),
                )
        except httpx.HTTPError as e:
            _log.warning("evidence emit_reasoning_step transport_error=%s", e)
