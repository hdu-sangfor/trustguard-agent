"""
R8b：编排器就绪检查（供 orch_staging_readiness.py 与单测复用）。
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class _GetClient(Protocol):
    def get(self, url: str) -> Any: ...


def _orch_url(base: str, path: str) -> str:
    """base 为空时仅返回以 / 开头的路径（供 Starlette/FastAPI TestClient）。"""
    p = path if path.startswith("/") else f"/{path}"
    b = (base or "").strip().rstrip("/")
    if not b:
        return p
    return f"{b}{p}"


def collect_readiness_errors(
    client: _GetClient,
    base: str,
    *,
    fail_on_sli_alerts: bool,
    max_compile_fail_rate: float | None = None,
) -> list[str]:
    """返回错误列表；空表示通过。"""
    errors: list[str] = []

    h = client.get(_orch_url(base, "/health"))
    if h.status_code != 200:
        errors.append(f"/health -> {h.status_code}")
    else:
        try:
            hj = h.json()
            if hj.get("status") != "ok":
                errors.append(f"/health body status={hj.get('status')!r}")
        except Exception as e:
            errors.append(f"/health json: {e}")

    s = client.get(_orch_url(base, "/v1/orchestrator/sli/snapshot"))
    if s.status_code != 200:
        errors.append(f"/v1/orchestrator/sli/snapshot -> {s.status_code}")
    else:
        try:
            sj = s.json()
            if sj.get("schema_version") != "orch-sli-v1":
                errors.append("sli snapshot schema_version mismatch")
            if "counters" not in sj or "alerts" not in sj:
                errors.append("sli snapshot missing counters/alerts")
            if fail_on_sli_alerts and sj.get("alerts"):
                firing = [a for a in sj["alerts"] if isinstance(a, dict) and a.get("firing")]
                if firing:
                    errors.append(f"sli alerts firing: {json.dumps(firing, ensure_ascii=False)[:500]}")
            if max_compile_fail_rate is not None:
                cfr = sj.get("compile_fail_rate")
                try:
                    cfr_v = float(cfr)
                except (TypeError, ValueError):
                    errors.append(f"compile_fail_rate invalid: {cfr!r}")
                else:
                    if cfr_v > float(max_compile_fail_rate):
                        errors.append(
                            f"compile_fail_rate too high: {cfr_v:.4f} > {float(max_compile_fail_rate):.4f}"
                        )
        except Exception as e:
            errors.append(f"sli json: {e}")

    m = client.get(_orch_url(base, "/v1/orchestrator/mq-status"))
    if m.status_code != 200:
        errors.append(f"/v1/orchestrator/mq-status -> {m.status_code}")
    else:
        try:
            mj: dict[str, Any] = m.json()
            mode = (mj.get("mode") or "").strip().lower()
            if mode == "mq" and mj.get("error"):
                errors.append(f"mq-status error: {mj.get('error')}")
        except Exception as e:
            errors.append(f"mq-status json: {e}")

    return errors


def collect_evidence_readiness_errors(client: _GetClient, base: str) -> list[str]:
    """
    Evidence探活（可选）：GET /health 须 200 且 body.status == ok。
    base 为Evidence根 URL，如 http://host:18103；与 collect_readiness_errors 共用 _orch_url 规则。
    """
    errors: list[str] = []
    h = client.get(_orch_url(base, "/health"))
    if h.status_code != 200:
        errors.append(f"evidence /health -> {h.status_code}")
    else:
        try:
            hj = h.json()
            if hj.get("status") != "ok":
                errors.append(f"evidence /health body status={hj.get('status')!r}")
        except Exception as e:
            errors.append(f"evidence /health json: {e}")
    return errors
