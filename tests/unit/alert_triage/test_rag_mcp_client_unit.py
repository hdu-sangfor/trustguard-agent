"""Alert-triage RAG MCP adapter tests."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator" / "app"))

from clients import rag_client
from app.knowledge.models import KnowledgeScope, RetrievalMode


def _settings(*, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled,
        workspace_id="workspace-test",
        limit=5,
        scope=KnowledgeScope.PENETRATION,
        mode=RetrievalMode.COMPREHENSIVE,
    )


def _hit() -> SimpleNamespace:
    return SimpleNamespace(
        resource_uri="trustguard-rag://penetration/resources/krf1.test",
        resource_ref="krf1.test",
        source_revision=3,
        content_hash="sha256:" + "a" * 64,
        title="ATT&CK T1059",
        score=0.9,
    )


def _resource(*, resource_ref: str = "krf1.test") -> SimpleNamespace:
    return SimpleNamespace(
        resource_ref=resource_ref,
        scope="penetration",
        source_revision=3,
        content_hash="sha256:" + "a" * 64,
        text="PowerShell 编码命令需要结合父进程和网络行为确认。",
        title="ATT&CK T1059",
        filename="attack.md",
        source_uri="https://example.test/attack",
        page_no=2,
    )


@pytest.mark.asyncio
async def test_query_rag_uses_configured_scope_and_alert_triage_workflow_identity():
    gateway = SimpleNamespace(
        search=AsyncMock(return_value=SimpleNamespace(status="ok", hits=[_hit()])),
        read_resource=AsyncMock(return_value=_resource()),
    )
    with patch.object(rag_client.KnowledgeMcpSettings, "from_env", return_value=_settings()), patch.object(
        rag_client, "get_knowledge_gateway", return_value=gateway
    ):
        result = await rag_client.query_rag(["PowerShell 告警怎么研判？"], {"task_id": "at-1"})

    assert result.degraded is False
    assert result.citations[0]["resource_uri"].startswith("trustguard-rag://")
    request = gateway.search.await_args.args[0]
    context = gateway.search.await_args.kwargs["context"]
    assert request.scope.value == "penetration"
    assert request.mode.value == "comprehensive"
    assert context.workflow_type == "alert-triage"
    assert context.task_id == "at-1"


@pytest.mark.asyncio
async def test_query_rag_fails_open_when_mcp_disabled():
    with patch.object(rag_client.KnowledgeMcpSettings, "from_env", return_value=_settings(enabled=False)):
        result = await rag_client.query_rag(["query"])
    assert result.degraded is True
    assert result.reason == "RAG_MCP_DISABLED"


@pytest.mark.asyncio
async def test_query_rag_rejects_resource_mismatch():
    gateway = SimpleNamespace(
        search=AsyncMock(return_value=SimpleNamespace(status="ok", hits=[_hit()])),
        read_resource=AsyncMock(return_value=_resource(resource_ref="krf1.other")),
    )
    with patch.object(rag_client.KnowledgeMcpSettings, "from_env", return_value=_settings()), patch.object(
        rag_client, "get_knowledge_gateway", return_value=gateway
    ):
        result = await rag_client.query_rag(["query"])
    assert result.degraded is True
    assert result.reason == "MCP_RESOURCE_REF_MISMATCH"


def test_build_questions_uses_official_xdr_alert_fields():
    questions = rag_client.build_questions_from_alert({
        "name": "疑似 WebShell 上传并执行系统命令",
        "threatType": "WebShell",
        "description": "检测到危险文件上传后启动命令解释器",
    })

    assert "WebShell" in questions[0]
    assert "上传" in questions[0]
    assert any("命令解释器" in question for question in questions)
    assert any("CWE-434" in question for question in questions)
    assert any("CWE-78" in question for question in questions)
