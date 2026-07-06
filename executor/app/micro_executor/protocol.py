"""
旁路嗅探 stderr 单行协议（S-06）：run.py 落盘成功后写入 sys.stderr，Worker Daemon 解析。

单行格式: ``[__SYS_ARTIFACT__]::{json}``
JSON 字段: v, artifact_ref, skill_id?, type_tag?, request_id?
"""
from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1
ARTIFACT_NOTICE_PREFIX = "[__SYS_ARTIFACT__]::"


def build_artifact_notice(
    artifact_ref: str,
    *,
    skill_id: str = "",
    type_tag: str = "",
    request_id: str = "",
) -> str:
    """生成一行 Daemon 可解析的 stderr 通知（勿含换行以外控制字符）。"""
    ref = (artifact_ref or "").strip()
    if not ref:
        raise ValueError("artifact_ref must be non-empty")
    payload: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "artifact_ref": ref,
        "skill_id": (skill_id or "").strip(),
        "type_tag": (type_tag or "").strip(),
        "request_id": (request_id or "").strip(),
    }
    return ARTIFACT_NOTICE_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_artifact_notice_line(line: str) -> dict[str, Any] | None:
    """
    从一行 stderr 解析嗅探载荷；不匹配或非法则返回 None。
    Daemon 与单测共用，避免协议分叉。
    """
    s = (line or "").strip()
    if not s.startswith(ARTIFACT_NOTICE_PREFIX):
        return None
    raw = s[len(ARTIFACT_NOTICE_PREFIX) :]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        if int(data.get("v", -1)) != PROTOCOL_VERSION:
            return None
    except (TypeError, ValueError):
        return None
    ref = str(data.get("artifact_ref", "")).strip()
    if not ref:
        return None
    return data
