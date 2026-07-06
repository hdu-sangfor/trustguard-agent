"""checkpoint_client：Evidence断点 payload 与 ORCH_CHECKPOINT_INCLUDE_PHASE_CLOCK。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.orchestrator_test_env import orchestrator_sys_path_isolated


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.captured: dict = {}

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def put(self, url: str, json=None, timeout=None):
        self.captured["url"] = url
        self.captured["json"] = json
        return _FakeResponse()


@pytest.mark.asyncio
async def test_checkpoint_client_includes_phase_clock_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_CHECKPOINT_INCLUDE_PHASE_CLOCK", raising=False)
    fake = _FakeAsyncClient()
    with orchestrator_sys_path_isolated():
        with patch("app.clients.checkpoint_client.httpx.AsyncClient", return_value=fake):
            from app.clients.checkpoint_client import save_checkpoint

            await save_checkpoint(
                "tid-1",
                "RECON",
                "RUNNING",
                {},
                "hist",
                phase_start_at="2020-01-01T00:00:00Z",
                current_phase_duration_limit_sec=120,
            )
    body = fake.captured["json"]
    assert body["phase_start_at"] == "2020-01-01T00:00:00Z"
    assert body["current_phase_duration_limit_sec"] == 120


@pytest.mark.asyncio
async def test_checkpoint_client_omits_phase_clock_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_CHECKPOINT_INCLUDE_PHASE_CLOCK", "0")
    fake = _FakeAsyncClient()
    with orchestrator_sys_path_isolated():
        with patch("app.clients.checkpoint_client.httpx.AsyncClient", return_value=fake):
            from app.clients.checkpoint_client import save_checkpoint

            await save_checkpoint(
                "tid-2",
                "RECON",
                "RUNNING",
                {},
                "hist",
                phase_start_at="2020-01-01T00:00:00Z",
                current_phase_duration_limit_sec=120,
            )
    body = fake.captured["json"]
    assert "phase_start_at" not in body
    assert "current_phase_duration_limit_sec" not in body
    assert body["current_phase"] == "RECON"
