from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.core.decision_context import build_decision_context
from app.knowledge.config import KnowledgeMcpSettings
from app.knowledge.gateway import KnowledgeGateway
from app.knowledge.mcp_client import (
    KnowledgeCallContext,
    McpKnowledgeTransport,
    McpKnowledgeTransportError,
)
from app.knowledge.models import (
    KnowledgeResource,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.knowledge.shadow import run_penetration_shadow_search

_CONTRACTS = Path(__file__).parents[3] / "contracts" / "rag_mcp" / "v1"


def _settings() -> KnowledgeMcpSettings:
    return KnowledgeMcpSettings.from_env(
        {
            "KNOWLEDGE_MCP_ENABLED": "true",
            "KNOWLEDGE_MCP_SHADOW_MODE": "true",
            "KNOWLEDGE_MCP_URL": "http://rag.test/mcp",
            "KNOWLEDGE_MCP_SCOPE": "penetration",
            "KNOWLEDGE_MCP_MODE": "comprehensive",
            "KNOWLEDGE_MCP_MAX_RETRIES": "0",
        }
    )


def _response() -> KnowledgeSearchResponse:
    return KnowledgeSearchResponse.model_validate_json(
        (_CONTRACTS / "knowledge_search_response.json").read_text(encoding="utf-8")
    )


def test_agent_models_consume_rag_v1_contract_fixtures() -> None:
    request = KnowledgeSearchRequest.model_validate_json(
        (_CONTRACTS / "knowledge_search_request.json").read_text(encoding="utf-8")
    )
    response = _response()
    resource = KnowledgeResource.model_validate_json(
        (_CONTRACTS / "knowledge_resource.json").read_text(encoding="utf-8")
    )

    assert request.scope.value == "penetration"
    assert response.hits[0].resource_ref == resource.resource_ref
    assert response.hits[0].content_hash == resource.content_hash


@pytest.mark.asyncio
async def test_gateway_retries_retryable_transport_failures() -> None:
    class FlakyTransport:
        calls = 0

        async def search(self, request, *, context):
            self.calls += 1
            if self.calls == 1:
                raise McpKnowledgeTransportError(
                    "MCP_UNAVAILABLE",
                    "temporary failure",
                    retryable=True,
                )
            return _response()

        async def aclose(self):
            return None

    transport = FlakyTransport()
    gateway = KnowledgeGateway(
        transport=transport,
        timeout_seconds=1,
        max_retries=1,
    )
    request = KnowledgeSearchRequest.model_validate_json(
        (_CONTRACTS / "knowledge_search_request.json").read_text(encoding="utf-8")
    )

    response = await gateway.search(
        request,
        context=KnowledgeCallContext(
            task_id="task-retry",
            request_id="request-retry",
            workflow_type="penetration",
            workspace_id="default",
        ),
    )

    assert response.status == "ok"
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_mcp_transport_injects_per_call_identity_and_trace_headers() -> None:
    settings = replace(_settings(), access_token="short-lived-jwt")
    transport = McpKnowledgeTransport(settings)
    context = KnowledgeCallContext(
        task_id="task-header",
        request_id="request-header",
        workflow_type="penetration",
        workspace_id="default",
    )
    context_token = transport._context_headers.set(transport._headers(context))
    try:
        request = httpx.Request("POST", settings.endpoint)
        await transport._inject_context_headers(request)
    finally:
        transport._context_headers.reset(context_token)
        await transport.aclose()

    assert request.headers["X-Request-ID"] == "request-header"
    assert request.headers["X-Task-ID"] == "task-header"
    assert request.headers["X-Workflow-Type"] == "penetration"
    assert request.headers["X-Workspace-ID"] == "default"
    assert request.headers["Authorization"] == "Bearer short-lived-jwt"


@pytest.mark.asyncio
async def test_shadow_search_records_trace_without_mutating_kb_hits() -> None:
    class FakeGateway:
        calls = 0

        async def search(self, request, *, context):
            self.calls += 1
            assert request.scope.value == "penetration"
            assert context.task_id == "task-shadow"
            return _response()

    gateway = FakeGateway()
    events = []

    async def capture(event):
        events.append(event)

    original_hits = [{"source": "experience", "chunk_id": "chk-existing1234567890"}]
    target_context = {
        "kb_hits": original_hits.copy(),
        "_current_todo": {"id": "todo-1", "name": "识别 Shiro"},
        "fingerprints": ["Apache Shiro"],
    }
    await run_penetration_shadow_search(
        task_id="task-shadow",
        phase="VULN_SCAN",
        query_text="Apache Shiro RememberMe 风险验证",
        target_context=target_context,
        settings=_settings(),
        gateway=gateway,
        trace_emitter=capture,
    )

    assert gateway.calls == 1
    assert target_context["kb_hits"] == original_hits
    assert target_context["_knowledge_mcp_shadow"]["status"] == "ok"
    decision_context, _ = build_decision_context(
        target_context,
        history_summary="",
        phase="VULN_SCAN",
    )
    assert "krf1.contract-fixture" not in json.dumps(decision_context)
    assert [event.event_type for event in events] == [
        "KNOWLEDGE_TRIGGERED",
        "MCP_TOOL_CALLED",
    ]

    await run_penetration_shadow_search(
        task_id="task-shadow",
        phase="VULN_SCAN",
        query_text="历史摘要变化但安全上下文未变化",
        target_context=target_context,
        settings=_settings(),
        gateway=gateway,
        trace_emitter=capture,
    )
    assert gateway.calls == 1
    assert events[-1].event_type == "KNOWLEDGE_SKIPPED_CACHE"


@pytest.mark.asyncio
async def test_shadow_search_fails_open_and_records_sanitized_error() -> None:
    class FailingGateway:
        async def search(self, request, *, context):
            raise McpKnowledgeTransportError(
                "MCP_TIMEOUT",
                "timeout",
                retryable=True,
            )

    events = []

    async def capture(event):
        events.append(event)

    target_context = {"_current_todo": {"id": "todo-2"}}
    await run_penetration_shadow_search(
        task_id="task-fail-open",
        phase="RECON",
        query_text="Web 技术栈侦察",
        target_context=target_context,
        settings=_settings(),
        gateway=FailingGateway(),
        trace_emitter=capture,
    )

    assert target_context["_knowledge_mcp_shadow"]["status"] == "failed"
    assert events[-1].event_type == "MCP_TOOL_FAILED"
    assert events[-1].payload["code"] == "MCP_TIMEOUT"
    assert "query" not in json.dumps(events[-1].payload)
