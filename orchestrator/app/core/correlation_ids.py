"""
编排/派发/Trace 统一关联字段（nf-correlation-trace-logs）。

在 TraceEvent.payload 与结构化错误中写入同一套键，便于日志检索与对账：
task_id（必有）、request_id（有则写）、plan_id（Plan 模式有则写）。
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping


def correlation_dict(
    task_id: str,
    *,
    request_id: str | None = None,
    plan_id: str | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {"task_id": (task_id or "").strip()}
    rid = (request_id or "").strip()
    if rid:
        out["request_id"] = rid
    pid = (plan_id or "").strip()
    if pid:
        out["plan_id"] = pid
    return out


def attach_correlation(
    payload: MutableMapping[str, Any],
    task_id: str,
    *,
    request_id: str | None = None,
    plan_id: str | None = None,
) -> None:
    """就地写入 payload[\"correlation\"]；不覆盖已有非空 correlation 时可先检查（当前为覆盖式，保证最新）。"""
    payload["correlation"] = correlation_dict(task_id, request_id=request_id, plan_id=plan_id)


def correlation_log_suffix(corr: Mapping[str, Any] | None) -> str:
    """供 logger 拼接：task_id=… request_id=… plan_id=…"""
    if not corr:
        return ""
    parts = []
    for k in ("task_id", "request_id", "plan_id"):
        v = corr.get(k)
        if v is not None and str(v).strip():
            parts.append(f"{k}={v}")
    return " ".join(parts)
