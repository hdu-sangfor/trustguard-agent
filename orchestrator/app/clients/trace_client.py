import logging
import os

import httpx

from app.clients.evidence_http_error import evidence_http_error_detail
from app.models import TraceEvent

EVIDENCE_BASE_URL = os.getenv("EVIDENCE_BASE_URL", "http://localhost:18103")

_log = logging.getLogger(__name__)


async def emit_trace(event: TraceEvent) -> None:
    """向 Trace Service 发送事件，失败时静默忽略（MVP），但打 warning 便于关联 evidence trace_id。"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{EVIDENCE_BASE_URL}/v1/events",
                json=event.model_dump(),
                timeout=5.0,
            )
            if resp.status_code >= 400:
                _log.warning(
                    "evidence emit_trace task_id=%s %s",
                    event.task_id,
                    evidence_http_error_detail(resp),
                )
        except httpx.HTTPError as e:
            _log.warning("evidence emit_trace transport_error=%s", e)

