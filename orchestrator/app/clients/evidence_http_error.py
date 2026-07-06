"""
从Evidence HTTP 响应解析 `EvidenceErrorBody`（code / trace_id / field_errors），供日志关联排障。
"""
from __future__ import annotations

import json
from typing import Any

import httpx


def evidence_http_error_detail(resp: httpx.Response) -> str:
    """返回单行摘要，便于 logger.warning 拼接。"""
    try:
        data: Any = resp.json()
    except Exception:
        preview = (resp.text or "")[:400]
        return f"status={resp.status_code} body_preview={preview!r}"

    if not isinstance(data, dict):
        return f"status={resp.status_code}"

    code = data.get("code")
    tid = data.get("trace_id")
    if code or tid:
        parts: list[str] = [f"status={resp.status_code}"]
        if code:
            parts.append(f"code={code!r}")
        if tid:
            parts.append(f"trace_id={tid!r}")
        msg = data.get("message")
        if isinstance(msg, str) and msg:
            parts.append(f"message={msg[:240]!r}")
        fe = data.get("field_errors")
        if isinstance(fe, dict) and fe:
            try:
                parts.append("field_errors=" + json.dumps(fe, ensure_ascii=False)[:400])
            except Exception:
                parts.append(f"field_errors={fe!r}")
        return " ".join(parts)

    try:
        return f"status={resp.status_code} json={json.dumps(data, ensure_ascii=False)[:400]}"
    except Exception:
        return f"status={resp.status_code}"
