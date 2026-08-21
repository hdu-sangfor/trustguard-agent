from __future__ import annotations

import re
from typing import Any


_ALERT_UUID = re.compile(r"(?<![A-Za-z0-9])alert[-_][A-Za-z0-9._:-]+", re.IGNORECASE)
_SCENARIO_ID = re.compile(
    r"(?:scenario(?:Id|_id)?|场景(?:ID|编号)?)\s*[:：=]?\s*([A-Za-z0-9._:-]+)",
    re.IGNORECASE,
)
_RAG_ENABLED = ("启用rag", "开启rag", "使用rag", "enable rag", "with rag")
_RAG_DISABLED = ("禁用rag", "关闭rag", "不启用rag", "without rag", "disable rag")


def parse_intent(message: str) -> dict[str, Any]:
    alert_match = _ALERT_UUID.search(message)
    scenario_match = _SCENARIO_ID.search(message)
    normalized = " ".join(message.lower().split())
    enable_rag = any(marker in normalized for marker in _RAG_ENABLED)
    if any(marker in normalized for marker in _RAG_DISABLED):
        enable_rag = False
    return {
        "workflow_id": "alert_triage",
        "alert_uuid": alert_match.group(0) if alert_match else "",
        "scenario_id": scenario_match.group(1) if scenario_match else None,
        "enable_rag": enable_rag,
        "caller_notes": message[:4000],
    }
