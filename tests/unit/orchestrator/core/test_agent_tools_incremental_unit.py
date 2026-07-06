"""编排器 apply_execution_result：incremental_artifacts → evidence INCREMENTAL_ARTIFACT + 黑板。"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("KB_ENABLED", "false")

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core.agent_tools import SkillCallContext, SkillExecutor  # noqa: E402
from app.core.framework_detect import detect_framework_from_context  # noqa: E402
from app.models import ExecuteSkillResponse, Phase, TaskState  # noqa: E402


@pytest.mark.asyncio
async def test_apply_incremental_emits_incremental_artifact_trace() -> None:
    state = TaskState("t1", "n", "https://x.test")
    ctx = SkillCallContext(
        task_id="t1",
        phase=Phase.RECON,
        skill_id="nmap",
        target="https://x.test",
        params={},
        allowed_target="https://x.test",
        todo_id="td-1",
    )
    er = ExecuteSkillResponse(
        status="SUCCESS",
        parsed_artifacts={},
        incremental_artifacts=[{"kind": "finding", "summary": "admin path", "severity": "high"}],
    )
    payload: dict = {"exec_result": er, "resolved_artifacts": {}}
    emitted: list = []

    async def _capture(ev):  # noqa: ANN001
        emitted.append(ev)

    with (
        patch("app.core.agent_tools.put_context", new=AsyncMock()),
        patch("app.core.agent_tools.put_artifacts_summary", new=AsyncMock()),
        patch("app.core.agent_tools.write_task_context", MagicMock()),
        patch("app.core.agent_tools.write_memory_parsed_artifact", return_value=""),
        patch("app.core.agent_tools.write_artifact", return_value=""),
        patch("app.clients.trace_client.emit_trace", new=_capture),
    ):
        se = SkillExecutor()
        await se.apply_execution_result(state, ctx, payload)

    inc_events = [e for e in emitted if getattr(e, "event_type", "") == "INCREMENTAL_ARTIFACT"]
    assert len(inc_events) == 1
    assert inc_events[0].payload.get("kind") == "finding"
    assert inc_events[0].payload.get("summary") == "admin path"
    bb = state.target_context.get("_tactical_incremental_artifacts")
    assert isinstance(bb, list) and len(bb) == 1
    assert bb[0].get("skill_id") == "nmap"
    assert bb[0].get("todo_id") == "td-1"


@pytest.mark.asyncio
async def test_http_enum_result_preserves_namespaced_framework_evidence() -> None:
    state = TaskState("t-http-enum", "n", "http://host.docker.internal:8080/")
    ctx = SkillCallContext(
        task_id="t-http-enum",
        phase=Phase.RECON,
        skill_id="http-enum",
        target="http://host.docker.internal:8080/",
        params={},
        allowed_target="http://host.docker.internal:8080/",
    )
    raw = 'HTTP/1.1 200 OK\n\n<title>Struts2 Showcase - Fileupload sample</title><form action="/doUpload.action">'
    er = ExecuteSkillResponse(
        status="SUCCESS",
        parsed_artifacts={
            "url": "http://host.docker.internal:8080/",
            "http_status": "200",
            "title": "Struts2 Showcase - Fileupload sample",
            "raw_preview": raw,
            "returncode": 0,
        },
    )
    payload: dict = {"exec_result": er, "resolved_artifacts": dict(er.parsed_artifacts)}

    with (
        patch("app.core.agent_tools.put_context", new=AsyncMock()),
        patch("app.core.agent_tools.put_artifacts_summary", new=AsyncMock()),
        patch("app.core.agent_tools.write_task_context", MagicMock()),
        patch("app.core.agent_tools.write_memory_parsed_artifact", return_value=""),
        patch("app.core.agent_tools.write_artifact", return_value=""),
        patch("app.core.agent_tools.ensure_http_fallback_baseline", new=AsyncMock(return_value=None)),
    ):
        se = SkillExecutor()
        await se.apply_execution_result(state, ctx, payload)

    assert state.target_context.get("http-enum_title") == "Struts2 Showcase - Fileupload sample"
    assert "/doUpload.action" in str(state.target_context.get("http-enum_raw_preview") or "")
    fw, evidence = detect_framework_from_context(state.target_context)
    assert fw == "struts2"
    assert evidence
