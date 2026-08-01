"""Unit tests for CoT step type registry and emit_cot_step redaction."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.paths import REPO_ROOT

_orch = str(REPO_ROOT / "orchestrator")
for _service in ("executor", "orchestrator", "gateway", "evidence"):
    _path = str(REPO_ROOT / _service)
    while _path in sys.path:
        sys.path.remove(_path)
sys.path.insert(0, _orch)
for _name in list(sys.modules):
    if _name == "app" or _name.startswith("app."):
        del sys.modules[_name]

from app.core.reasoning_emit import emit_cot_step  # noqa: E402
from app.reasoning_steps import (  # noqa: E402
    STEP_TYPES,
    is_valid_step_type,
    label_zh,
)


def test_registry_has_eight_types():
    assert len(STEP_TYPES) == 8
    assert is_valid_step_type("TOOL_CALL")
    assert label_zh("RAG_RETRIEVAL") == "RAG 检索"


@pytest.mark.asyncio
async def test_emit_cot_step_redacts_token_in_payload():
    captured = []

    async def _capture(step):
        captured.append(step)

    with patch("app.core.reasoning_emit.emit_reasoning_step", side_effect=_capture):
        with patch("app.core.reasoning_emit.trace_redact_sensitive_enabled", return_value=True):
            await emit_cot_step(
                task_id="task-1",
                step_type="TOOL_CALL",
                status="SUCCEEDED",
                summary="Bearer super-secret-token-value",
                payload={"api_token": "secret-value", "tool_name": "nmap"},
            )
    assert len(captured) == 1
    step = captured[0]
    assert step.trace_id == "task-1"
    assert step.task_id == "task-1"
    assert step.payload.get("api_token") == "[REDACTED]"
    assert "Bearer [REDACTED]" in step.summary
    assert step.payload.get("tool_name") == "nmap"


@pytest.mark.asyncio
async def test_emit_cot_step_skips_invalid_type():
    with patch("app.core.reasoning_emit.emit_reasoning_step", new_callable=AsyncMock) as m:
        await emit_cot_step(task_id="task-1", step_type="NOPE", status="SUCCEEDED")
        m.assert_not_called()
