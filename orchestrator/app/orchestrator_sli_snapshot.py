"""
R8a：SLI 快照组装（可选 RabbitMQ Management 队列深度）与环境阈值告警评估。
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from app.orchestrator_sli_metrics import snapshot_counters


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


async def fetch_mq_execute_queue_snapshot() -> dict[str, Any]:
    """与 /v1/orchestrator/mq-status 同源字段，供 SLI 与告警使用。"""
    mode = (os.getenv("EXECUTION_DISPATCH_MODE") or "http").strip().lower()
    broker_url = (os.getenv("MQ_BROKER_URL") or "").strip()
    topic = (os.getenv("MQ_TOPIC_AGENT") or "execute_tasks_agent").strip() or "execute_tasks_agent"
    out: dict[str, Any] = {"mode": mode, "queue": topic}
    if mode != "mq" or not broker_url:
        out["message"] = "not in mq mode or MQ_BROKER_URL unset"
        return out
    try:
        u = urlparse(broker_url)
        host = u.hostname or "localhost"
        management_url = f"http://{host}:15672/api/queues/%2F/{topic}"
        auth = (u.username or "guest", u.password or "guest")
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(management_url, auth=auth)
            r.raise_for_status()
            data = r.json()
        out["messages_ready"] = int(data.get("messages_ready") or 0)
        out["messages_unacknowledged"] = int(data.get("messages_unacknowledged") or 0)
        out["consumers"] = int(data.get("consumers") or 0)
        out["message_stats"] = data.get("message_stats")
        return out
    except Exception as e:
        out["error"] = str(e)
        out["message"] = "failed to reach RabbitMQ Management API (is management plugin enabled?)"
        return out


def eval_sli_threshold_alerts(counters: dict[str, Any], mq: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    进程内阈值评估（便于对接外部告警或 CI 门禁）。
    环境变量（均可选）：
    - ORCH_SLI_ALERT_COMPILE_FAIL_RATE + ORCH_SLI_ALERT_COMPILE_MIN_SAMPLES
    - ORCH_SLI_ALERT_TICK_FAIL_RATE + ORCH_SLI_ALERT_TICK_MIN_SAMPLES
    - ORCH_SLI_ALERT_TICK_AVG_MS
    - ORCH_SLI_ALERT_MQ_MESSAGES_READY
    - ORCH_SLI_ALERT_KB_PROMOTION_FAIL_RATE + ORCH_SLI_ALERT_KB_PROMOTION_MIN_ATTEMPTS
    """
    alerts: list[dict[str, Any]] = []
    ic = counters.get("instruction_compile") or {}
    total_c = int(ic.get("compile_total") or 0)
    rate_c = ic.get("compile_fail_rate")
    min_c = max(1, _int_env("ORCH_SLI_ALERT_COMPILE_MIN_SAMPLES", 20))
    thr_c = _float_env("ORCH_SLI_ALERT_COMPILE_FAIL_RATE", -1.0)
    if thr_c >= 0 and total_c >= min_c and isinstance(rate_c, (int, float)) and float(rate_c) >= thr_c:
        alerts.append(
            {
                "name": "compile_fail_rate_high",
                "firing": True,
                "detail": f"compile_fail_rate={rate_c} threshold={thr_c} samples={total_c}",
            }
        )

    ot = counters.get("orchestrator_tick") or {}
    tt = int(ot.get("tick_attempts_total") or 0)
    rate_t = ot.get("tick_fail_rate")
    min_t = max(1, _int_env("ORCH_SLI_ALERT_TICK_MIN_SAMPLES", 10))
    thr_t = _float_env("ORCH_SLI_ALERT_TICK_FAIL_RATE", -1.0)
    if thr_t >= 0 and tt >= min_t and isinstance(rate_t, (int, float)) and float(rate_t) >= thr_t:
        alerts.append(
            {
                "name": "tick_fail_rate_high",
                "firing": True,
                "detail": f"tick_fail_rate={rate_t} threshold={thr_t} samples={tt}",
            }
        )

    avg_ms = ot.get("tick_duration_ms_avg")
    thr_avg = _float_env("ORCH_SLI_ALERT_TICK_AVG_MS", -1.0)
    if thr_avg > 0 and isinstance(avg_ms, (int, float)) and float(avg_ms) >= thr_avg:
        alerts.append(
            {
                "name": "tick_latency_avg_high",
                "firing": True,
                "detail": f"tick_duration_ms_avg={avg_ms} threshold_ms={thr_avg}",
            }
        )

    if mq and "messages_ready" in mq:
        thr_q = _int_env("ORCH_SLI_ALERT_MQ_MESSAGES_READY", -1)
        if thr_q >= 0:
            ready = int(mq.get("messages_ready") or 0)
            if ready >= thr_q:
                alerts.append(
                    {
                        "name": "mq_queue_backlog_high",
                        "firing": True,
                        "detail": f"messages_ready={ready} threshold={thr_q}",
                    }
                )

    kb = counters.get("kb_experience_promotion") or {}
    kbo = int(kb.get("promoted_ok_total") or 0)
    kbf = int(kb.get("promoted_fail_total") or 0)
    kb_attempts = kbo + kbf
    kb_min = max(1, _int_env("ORCH_SLI_ALERT_KB_PROMOTION_MIN_ATTEMPTS", 8))
    kb_thr = _float_env("ORCH_SLI_ALERT_KB_PROMOTION_FAIL_RATE", -1.0)
    if kb_thr >= 0 and kb_attempts >= kb_min:
        kb_rate = kbf / kb_attempts if kb_attempts > 0 else 0.0
        if kb_rate >= kb_thr:
            alerts.append(
                {
                    "name": "kb_experience_promotion_fail_rate_high",
                    "firing": True,
                    "detail": (
                        f"kb_promotion_fail_rate={round(kb_rate, 6)} "
                        f"threshold={kb_thr} attempts={kb_attempts}"
                    ),
                }
            )

    return alerts


async def build_sli_snapshot(*, include_mq: bool = True) -> dict[str, Any]:
    from datetime import datetime

    ctr = snapshot_counters()
    mq: dict[str, Any] | None = None
    if include_mq:
        mq = await fetch_mq_execute_queue_snapshot()
    alerts = eval_sli_threshold_alerts(ctr, mq)
    hint = {
        "ORCH_SLI_ALERT_COMPILE_FAIL_RATE": os.getenv("ORCH_SLI_ALERT_COMPILE_FAIL_RATE", ""),
        "ORCH_SLI_ALERT_COMPILE_MIN_SAMPLES": os.getenv("ORCH_SLI_ALERT_COMPILE_MIN_SAMPLES", ""),
        "ORCH_SLI_ALERT_TICK_FAIL_RATE": os.getenv("ORCH_SLI_ALERT_TICK_FAIL_RATE", ""),
        "ORCH_SLI_ALERT_TICK_MIN_SAMPLES": os.getenv("ORCH_SLI_ALERT_TICK_MIN_SAMPLES", ""),
        "ORCH_SLI_ALERT_TICK_AVG_MS": os.getenv("ORCH_SLI_ALERT_TICK_AVG_MS", ""),
        "ORCH_SLI_ALERT_MQ_MESSAGES_READY": os.getenv("ORCH_SLI_ALERT_MQ_MESSAGES_READY", ""),
        "ORCH_SLI_ALERT_KB_PROMOTION_FAIL_RATE": os.getenv("ORCH_SLI_ALERT_KB_PROMOTION_FAIL_RATE", ""),
        "ORCH_SLI_ALERT_KB_PROMOTION_MIN_ATTEMPTS": os.getenv("ORCH_SLI_ALERT_KB_PROMOTION_MIN_ATTEMPTS", ""),
    }
    return {
        "schema_version": "orch-sli-v1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "counters": ctr,
        "mq_execute_queue": mq,
        "alerts": alerts,
        "alert_thresholds_hint": {k: v for k, v in hint.items() if str(v).strip()},
    }
