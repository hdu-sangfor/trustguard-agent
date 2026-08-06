from __future__ import annotations

import re
from typing import Any


_ALERT_UUID = re.compile(r"^alert[-_][A-Za-z0-9._:-]+$", re.IGNORECASE)


def validate_draft(draft: dict[str, Any]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    warnings: list[str] = []
    alert_uuid = str(draft.get("alert_uuid") or "").strip()
    if not alert_uuid:
        missing.append("alert_uuid")
    elif not _ALERT_UUID.fullmatch(alert_uuid):
        missing.append("alert_uuid")
    if bool(draft.get("enable_rag")):
        warnings.append("已启用 RAG；知识引用仅作为辅助材料，最终结论仍需由 XDR 原始证据支持。")
    return missing, warnings
