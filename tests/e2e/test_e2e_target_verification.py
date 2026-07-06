"""
可选 E2E：Vulhub/Vulhub 风格「创建 → run → 事件」验证。

默认跳过：需Gateway可达（integration）且设置环境变量 ``E2E_TARGET_URL``（靶机 URL）。
本地启动与端口说明见 ``docs/operations.md``。

运行示例::

    E2E_TARGET_URL="http://localhost:8080" \
    pytest tests/e2e -m "e2e and integration" -v
"""

from __future__ import annotations

import os

import httpx
import pytest

INTEGRATION_TIMEOUT = float(os.getenv("INTEGRATION_HTTP_TIMEOUT", "60.0"))


@pytest.mark.e2e
@pytest.mark.integration
def test_v1_create_run_and_events_when_e2e_target_configured(gateway_url: str) -> None:
    """
    当设置 E2E_TARGET_URL 时，经 v1 API 创建任务、run 若干 tick、拉取事件，作为可复现用例的自动化钩子。
    不强制跑满至 REPORT（避免长跑）；若需完整至 DONE，可增大 E2E_MAX_TICKS 或用手册中的 run。
    """
    target = os.getenv("E2E_TARGET_URL", "").strip()
    if not target:
        pytest.skip("设置 E2E_TARGET_URL 为可达靶机 URL 后重试（见 docs/operations.md）")

    max_ticks = int(os.getenv("E2E_MAX_TICKS", "15"))
    base = gateway_url.rstrip("/")

    with httpx.Client(timeout=INTEGRATION_TIMEOUT) as client:
        cr = client.post(
            f"{base}/api/v1/tasks",
            json={
                "name": "e2e-vulhub-style",
                "target": target,
                "description": "automated optional e2e",
            },
        )
        assert cr.status_code == 200, cr.text
        body = cr.json()
        assert body.get("code") == "0", body
        data = body.get("data") or {}
        task_id = data.get("taskId") or data.get("task_id")
        assert task_id, f"no taskId in response: {body}"

        rr = client.post(f"{base}/api/v1/tasks/{task_id}/run", params={"maxTicks": max_ticks})
        assert rr.status_code == 200, rr.text

        er = client.get(f"{base}/api/v1/tasks/{task_id}/events", params={"limit": 500})
        assert er.status_code == 200, er.text
        ev_body = er.json()
        assert ev_body.get("code") == "0", ev_body
        events = ev_body.get("data") or []
        assert isinstance(events, list)
        assert len(events) >= 1, "expected at least one trace event after run"
