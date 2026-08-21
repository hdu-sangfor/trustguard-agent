from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def disable_external_trace_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep alert-triage unit tests independent from the Evidence service."""

    async def _discard_trace(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("clients.trace_client.emit_trace", _discard_trace)
