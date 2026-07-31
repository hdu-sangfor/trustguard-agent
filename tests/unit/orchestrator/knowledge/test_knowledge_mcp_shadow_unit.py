from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from app.core.chunk_store import read_chunk
from app.core.decision_context import build_decision_context
from app.core.instruction_compiler import compile_plan_item
from app.knowledge.config import KnowledgeMcpSettings
from app.knowledge.gateway import KnowledgeGateway
from app.knowledge.materializer import (
    KnowledgeMaterializationError,
    materialize_knowledge_hit,
)
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
from app.knowledge.shadow import (
    run_penetration_knowledge_search,
    run_penetration_shadow_search,
)
from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem

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
async def test_gateway_retries_retryable_resource_read_failures() -> None:
    class FlakyTransport:
        calls = 0

        async def read_resource(self, resource_uri, *, context):
            self.calls += 1
            if self.calls == 1:
                raise McpKnowledgeTransportError(
                    "MCP_UNAVAILABLE",
                    "temporary failure",
                    retryable=True,
                )
            return KnowledgeResource.model_validate_json(
                (_CONTRACTS / "knowledge_resource.json").read_text(encoding="utf-8")
            )

        async def aclose(self):
            return None

    transport = FlakyTransport()
    gateway = KnowledgeGateway(
        transport=transport,
        timeout_seconds=1,
        max_retries=1,
    )

    resource = await gateway.read_resource(
        _response().hits[0].resource_uri,
        context=KnowledgeCallContext(
            task_id="task-resource-retry",
            request_id="request-resource-retry",
            workflow_type="penetration",
            workspace_id="default",
        ),
    )

    assert resource.resource_ref == "krf1.contract-fixture"
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


@pytest.mark.asyncio
async def test_active_search_materializes_injects_and_reuses_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    class FakeGateway:
        search_calls = 0
        read_calls = 0

        async def search(self, request, *, context):
            self.search_calls += 1
            return _response()

        async def read_resource(self, resource_uri, *, context):
            self.read_calls += 1
            return KnowledgeResource.model_validate_json(
                (_CONTRACTS / "knowledge_resource.json").read_text(encoding="utf-8")
            )

    gateway = FakeGateway()
    events = []

    async def capture(event):
        events.append(event)

    settings = replace(
        _settings(),
        shadow_mode=False,
        materialize_enabled=True,
        inject_enabled=True,
    )
    original_experience = {
        "source": "experience",
        "chunk_id": "chk-existing1234567890",
    }
    target_context = {
        "kb_hits": [original_experience],
        "_current_todo": {"id": "todo-active", "name": "识别 Shiro"},
        "fingerprints": ["Apache Shiro"],
    }
    await run_penetration_knowledge_search(
        task_id="task-active",
        phase="VULN_SCAN",
        query_text="Apache Shiro RememberMe 风险验证",
        target_context=target_context,
        settings=settings,
        gateway=gateway,
        trace_emitter=capture,
    )

    assert gateway.search_calls == 1
    assert gateway.read_calls == 1
    assert target_context["kb_hits"][0] == original_experience
    mcp_hit = target_context["kb_hits"][1]
    assert mcp_hit["provider"] == "trustguard-rag-mcp"
    assert mcp_hit["chunk_id"].startswith("chk-")
    stored = read_chunk("task-active", mcp_hit["chunk_id"])
    assert stored is not None
    assert stored["meta"]["chunk_type"] == "rag_knowledge"
    assert stored["meta"]["tenant_id"] is None
    assert stored["content"]["text"].startswith("Apache Shiro")
    compiled = compile_plan_item(
        PlanItem(
            plan_id="plan-active",
            task_id="task-active",
            skill_id="ruleless-skill",
            plan_content="使用检索知识验证 Shiro",
            context_chunk_refs=[ContextChunkRef(chunk_id=mcp_hit["chunk_id"])],
            constraints=PlanConstraints(
                target_scope="host:example.test",
                timeout_seconds=120,
                max_parallelism=1,
            ),
            metadata={},
        ),
        verify_chunks=True,
    )
    assert compiled.ok and compiled.instruction is not None
    assert compiled.instruction.resolved_context_chunks[0].body["text"].startswith(
        "Apache Shiro"
    )
    assert [event.event_type for event in events] == [
        "KNOWLEDGE_TRIGGERED",
        "MCP_TOOL_CALLED",
        "KNOWLEDGE_MATERIALIZED",
        "KNOWLEDGE_INJECTED",
    ]
    assert events[1].payload["chunk_ids"] == [mcp_hit["chunk_id"]]
    assert events[1].payload["materialized_count"] == 1
    assert events[2].payload["request_id"] == events[1].payload["request_id"]
    assert events[2].payload["client_request_id"] == events[1].payload["client_request_id"]

    target_context["kb_hits"] = [original_experience]
    await run_penetration_knowledge_search(
        task_id="task-active",
        phase="VULN_SCAN",
        query_text="上下文摘要发生变化",
        target_context=target_context,
        settings=settings,
        gateway=gateway,
        trace_emitter=capture,
    )

    assert gateway.search_calls == 1
    assert gateway.read_calls == 1
    assert target_context["kb_hits"][1]["chunk_id"] == mcp_hit["chunk_id"]
    assert events[-1].event_type == "KNOWLEDGE_SKIPPED_CACHE"


@pytest.mark.asyncio
async def test_materializer_is_idempotent_and_binds_workspace_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    response = _response()
    resource = KnowledgeResource.model_validate_json(
        (_CONTRACTS / "knowledge_resource.json").read_text(encoding="utf-8")
    ).model_copy(update={"visibility": "workspace"})
    hit = response.hits[0].model_copy(update={"visibility": "workspace"})

    class FakeGateway:
        async def read_resource(self, resource_uri, *, context):
            return resource

    context = KnowledgeCallContext(
        task_id="task-workspace",
        request_id="request-workspace",
        workflow_type="penetration",
        workspace_id="workspace-a",
    )
    first = await materialize_knowledge_hit(
        task_id="task-workspace",
        search_scope=response.scope,
        search_content_revision=response.content_revision,
        hit=hit,
        gateway=FakeGateway(),
        context=context,
    )
    second = await materialize_knowledge_hit(
        task_id="task-workspace",
        search_scope=response.scope,
        search_content_revision=response.content_revision,
        hit=hit,
        gateway=FakeGateway(),
        context=context,
    )

    assert first.chunk_id == second.chunk_id
    assert first.source_ref.retrieved_at == second.source_ref.retrieved_at
    stored = read_chunk(
        "task-workspace",
        first.chunk_id,
        expect_tenant_id="workspace-a",
        require_tenant_when_bound=True,
    )
    assert stored is not None
    assert stored["meta"]["tenant_id"] == "workspace-a"


@pytest.mark.asyncio
async def test_materializer_rejects_resource_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    response = _response()
    bad_resource = KnowledgeResource.model_validate_json(
        (_CONTRACTS / "knowledge_resource.json").read_text(encoding="utf-8")
    ).model_copy(update={"content_hash": "sha256:" + "b" * 64})

    class FakeGateway:
        async def read_resource(self, resource_uri, *, context):
            return bad_resource

    with pytest.raises(KnowledgeMaterializationError, match="content hash mismatch"):
        await materialize_knowledge_hit(
            task_id="task-mismatch",
            search_scope=response.scope,
            search_content_revision=response.content_revision,
            hit=response.hits[0],
            gateway=FakeGateway(),
            context=KnowledgeCallContext(
                task_id="task-mismatch",
                request_id="request-mismatch",
                workflow_type="penetration",
                workspace_id="default",
            ),
        )


@pytest.mark.asyncio
async def test_active_search_resource_read_failure_is_fail_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    class FakeGateway:
        async def search(self, request, *, context):
            return _response()

        async def read_resource(self, resource_uri, *, context):
            raise McpKnowledgeTransportError(
                "MCP_RESOURCE_UNAVAILABLE",
                "resource failed",
                retryable=True,
            )

    events = []

    async def capture(event):
        events.append(event)

    original_hits = [{"source": "experience", "id": "exp-1"}]
    target_context = {
        "kb_hits": original_hits.copy(),
        "_current_todo": {"id": "todo-fail-open"},
        "fingerprints": ["Apache Shiro"],
    }
    await run_penetration_knowledge_search(
        task_id="task-resource-fail-open",
        phase="VULN_SCAN",
        query_text="Apache Shiro RememberMe 风险验证",
        target_context=target_context,
        settings=replace(
            _settings(),
            shadow_mode=False,
            materialize_enabled=True,
            inject_enabled=True,
        ),
        gateway=FakeGateway(),
        trace_emitter=capture,
    )

    assert target_context["kb_hits"] == original_hits
    assert target_context["_knowledge_mcp_shadow"]["materialization_errors"] == [
        {"code": "MCP_RESOURCE_UNAVAILABLE", "retryable": True}
    ]
    assert events[-2].event_type == "KNOWLEDGE_MATERIALIZED"
    assert events[-2].payload["failed_count"] == 1
    assert events[-1].event_type == "KNOWLEDGE_INJECTED"
    assert events[-1].payload["injected_count"] == 0
