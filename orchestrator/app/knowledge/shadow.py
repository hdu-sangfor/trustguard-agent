from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.clients.trace_client import emit_trace
from app.knowledge.config import KnowledgeMcpSettings
from app.knowledge.gateway import KnowledgeGateway, get_knowledge_gateway
from app.knowledge.materializer import materialize_knowledge_hit
from app.knowledge.mcp_client import KnowledgeCallContext, McpKnowledgeTransportError
from app.knowledge.models import KnowledgeSearchFilters, KnowledgeSearchRequest
from app.knowledge.query_builder import build_penetration_knowledge_query
from app.models import TraceEvent

_STATE_KEY = "_knowledge_mcp_shadow"
TraceEmitter = Callable[[TraceEvent], Awaitable[None]]


async def run_penetration_shadow_search(
    *,
    task_id: str,
    phase: str,
    query_text: str,
    target_context: dict[str, Any],
    settings: KnowledgeMcpSettings | None = None,
    gateway: KnowledgeGateway | None = None,
    trace_emitter: TraceEmitter = emit_trace,
) -> None:
    await run_penetration_knowledge_search(
        task_id=task_id,
        phase=phase,
        query_text=query_text,
        target_context=target_context,
        settings=settings,
        gateway=gateway,
        trace_emitter=trace_emitter,
    )


async def run_penetration_knowledge_search(
    *,
    task_id: str,
    phase: str,
    query_text: str,
    target_context: dict[str, Any],
    settings: KnowledgeMcpSettings | None = None,
    gateway: KnowledgeGateway | None = None,
    trace_emitter: TraceEmitter = emit_trace,
) -> None:
    active = settings or KnowledgeMcpSettings.from_env()
    if not active.enabled:
        return
    candidate = build_penetration_knowledge_query(
        phase=phase,
        query_text=query_text,
        target_context=target_context,
    )
    if candidate is None:
        return

    previous = target_context.get(_STATE_KEY)
    if (
        isinstance(previous, dict)
        and previous.get("fingerprint") == candidate.fingerprint
    ):
        if active.effective_inject_enabled:
            _inject_materialized_hits(
                target_context,
                previous.get("materialized_hits"),
            )
        if not previous.get("skip_traced"):
            previous["skip_traced"] = True
            await _emit(
                trace_emitter,
                task_id,
                "KNOWLEDGE_SKIPPED_CACHE",
                {
                    "phase": phase,
                    "scope": active.scope.value,
                    "fingerprint": candidate.fingerprint[:16],
                    "injected": active.effective_inject_enabled,
                },
            )
        return

    request_id = f"knw-{uuid.uuid4().hex[:20]}"
    await _emit(
        trace_emitter,
        task_id,
        "KNOWLEDGE_TRIGGERED",
        {
            "phase": phase,
            "scope": active.scope.value,
            "trigger": candidate.trigger,
            "fingerprint": candidate.fingerprint[:16],
            "request_id": request_id,
            "shadow": active.shadow_mode,
        },
    )
    request = KnowledgeSearchRequest(
        query=candidate.query,
        scope=active.scope,
        mode=active.mode,
        limit=active.limit,
        rewrite=active.rewrite,
        filters=KnowledgeSearchFilters(source_types=["document", "playbook"]),
    )
    started = time.perf_counter()
    active_gateway = gateway or get_knowledge_gateway(active)
    call_context = KnowledgeCallContext(
        task_id=task_id,
        request_id=request_id,
        workflow_type=active.workflow_type,
        workspace_id=active.workspace_id,
    )
    try:
        response = await active_gateway.search(request, context=call_context)
    except Exception as exc:  # noqa: BLE001 - optional knowledge must fail open
        error = exc if isinstance(exc, McpKnowledgeTransportError) else None
        target_context[_STATE_KEY] = {
            "fingerprint": candidate.fingerprint,
            "status": "failed",
            "request_id": request_id,
            "scope": active.scope.value,
            "skip_traced": False,
            "materialized_hits": [],
        }
        await _emit(
            trace_emitter,
            task_id,
            "MCP_TOOL_FAILED",
            {
                "phase": phase,
                "scope": active.scope.value,
                "tool": "knowledge_search",
                "request_id": request_id,
                "code": error.code if error else "MCP_UNEXPECTED_ERROR",
                "retryable": error.retryable if error else False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "shadow": active.shadow_mode,
            },
        )
        return

    hit_summaries = [
        {
            "resource_ref": hit.resource_ref,
            "resource_uri": hit.resource_uri,
            "source_revision": hit.source_revision,
            "content_hash": hit.content_hash,
            "score": hit.score,
            "source_type": hit.source_type,
        }
        for hit in response.hits
    ]
    materialized_hits: list[dict[str, Any]] = []
    source_refs: list[dict[str, Any]] = []
    chunk_ids: list[str] = []
    materialization_errors: list[dict[str, Any]] = []
    if active.materialize_enabled:
        for hit in response.hits[: active.materialize_limit]:
            try:
                materialized = await materialize_knowledge_hit(
                    task_id=task_id,
                    search_scope=response.scope,
                    search_content_revision=response.content_revision,
                    hit=hit,
                    gateway=active_gateway,
                    context=call_context,
                )
            except Exception as exc:  # noqa: BLE001 - retain other valid hits
                transport_error = (
                    exc if isinstance(exc, McpKnowledgeTransportError) else None
                )
                materialization_errors.append(
                    {
                        "code": (
                            transport_error.code
                            if transport_error
                            else "KNOWLEDGE_MATERIALIZATION_FAILED"
                        ),
                        "retryable": (
                            transport_error.retryable if transport_error else False
                        ),
                    }
                )
                continue
            materialized_hits.append(materialized.kb_hit)
            source_refs.append(materialized.source_ref.model_dump(mode="json"))
            chunk_ids.append(materialized.chunk_id)

    if active.effective_inject_enabled:
        _inject_materialized_hits(target_context, materialized_hits)

    target_context[_STATE_KEY] = {
        "fingerprint": candidate.fingerprint,
        "status": response.status,
        "request_id": response.request_id,
        "scope": response.scope,
        "content_revision": response.content_revision,
        "hits": hit_summaries,
        "materialized_hits": materialized_hits,
        "source_refs": source_refs,
        "materialization_errors": materialization_errors,
        "skip_traced": False,
    }
    payload = {
        "phase": phase,
        "scope": response.scope,
        "tool": "knowledge_search",
        "request_id": response.request_id,
        "client_request_id": request_id,
        "hit_count": len(response.hits),
        "latency_ms": response.latency_ms,
        "observed_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "status": response.status,
        "degraded_components": response.degraded_components,
        "resource_refs": [item["resource_ref"] for item in hit_summaries],
        "shadow": active.shadow_mode,
    }
    await _emit(trace_emitter, task_id, "MCP_TOOL_CALLED", payload)
    if active.materialize_enabled:
        await _emit(
            trace_emitter,
            task_id,
            "KNOWLEDGE_MATERIALIZED",
            {
                "phase": phase,
                "scope": response.scope,
                "chunk_ids": chunk_ids,
                "materialized_count": len(chunk_ids),
                "failed_count": len(materialization_errors),
                "shadow": active.shadow_mode,
            },
        )
    if active.effective_inject_enabled:
        await _emit(
            trace_emitter,
            task_id,
            "KNOWLEDGE_INJECTED",
            {
                "phase": phase,
                "scope": response.scope,
                "chunk_ids": chunk_ids,
                "injected_count": len(materialized_hits),
                "shadow": False,
            },
        )
    if response.degraded_components:
        await _emit(trace_emitter, task_id, "MCP_TOOL_DEGRADED", payload)


def _inject_materialized_hits(
    target_context: dict[str, Any],
    materialized_hits: Any,
) -> None:
    current = target_context.get("kb_hits")
    existing = list(current) if isinstance(current, list) else []
    retained = [
        item
        for item in existing
        if not (
            isinstance(item, dict)
            and item.get("provider") == "trustguard-rag-mcp"
        )
    ]
    if isinstance(materialized_hits, list):
        retained.extend(item for item in materialized_hits if isinstance(item, dict))
    target_context["kb_hits"] = retained
    target_context["kb_retrieval_count"] = len(retained)


async def _emit(
    emitter: TraceEmitter,
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    try:
        await emitter(
            TraceEvent(
                task_id=task_id,
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                event_type=event_type,
                source_module="orchestrator.knowledge",
                payload=payload,
            )
        )
    except Exception:  # noqa: BLE001 - observability must not break the workflow
        return
