from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.db import execute

log = logging.getLogger("trustguard.gateway.audit")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record_audit(
    event_type: str,
    actor: str,
    target: str = "",
    detail: str = "",
) -> None:
    """Persist a best-effort audit event in the existing trace event store."""
    try:
        execute(
            """
            INSERT INTO tg_trace_events
              (task_id, event_id, ts, event_type, source_module, payload, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                target or "platform",
                "evt-" + uuid.uuid4().hex[:12],
                _now_iso(),
                event_type,
                "gateway",
                json.dumps(
                    {"actor": actor, "target": target, "detail": detail},
                    ensure_ascii=False,
                ),
            ),
        )
    except Exception:
        log.debug("audit record skipped", exc_info=True)
