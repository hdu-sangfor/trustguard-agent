from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.chunk_store import ChunkStoreError, read_chunk, write_chunk
from app.knowledge.gateway import KnowledgeGateway
from app.knowledge.mcp_client import KnowledgeCallContext
from app.knowledge.models import (
    KnowledgeHit,
    KnowledgeResource,
    KnowledgeSourceRef,
)


class KnowledgeMaterializationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MaterializedKnowledge:
    chunk_id: str
    source_ref: KnowledgeSourceRef
    kb_hit: dict[str, Any]


async def materialize_knowledge_hit(
    *,
    task_id: str,
    search_scope: str,
    search_content_revision: str,
    hit: KnowledgeHit,
    gateway: KnowledgeGateway,
    context: KnowledgeCallContext,
) -> MaterializedKnowledge:
    resource = await gateway.read_resource(hit.resource_uri, context=context)
    _validate_resource(
        resource,
        hit=hit,
        search_scope=search_scope,
    )
    source_ref = KnowledgeSourceRef(
        scope=search_scope,
        resource_uri=hit.resource_uri,
        resource_ref=hit.resource_ref,
        search_content_revision=search_content_revision,
        resource_content_revision=resource.content_revision,
        source_revision=resource.source_revision,
        content_hash=resource.content_hash,
        document_id=resource.document_id,
        experience_id=resource.experience_id,
        source_uri=resource.source_uri,
        source_type=resource.source_type,
        workflow_type=resource.workflow_type,
        visibility=resource.visibility,
        retrieved_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    chunk_id = _chunk_id(source_ref)
    tenant_id = context.workspace_id if resource.visibility == "workspace" else None
    body = {
        "schema_version": "rag-knowledge-chunk-v1",
        "text": resource.text,
        "title": resource.title,
        "filename": resource.filename,
        "page_no": resource.page_no,
        "source_ref": source_ref.model_dump(mode="json"),
        "metadata": resource.metadata,
        "untrusted": True,
    }
    stored_body = await asyncio.to_thread(
        _write_idempotent,
        task_id,
        chunk_id,
        body,
        tenant_id,
    )
    stored_source_ref = KnowledgeSourceRef.model_validate(stored_body["source_ref"])
    metadata = resource.metadata if isinstance(resource.metadata, dict) else {}
    return MaterializedKnowledge(
        chunk_id=chunk_id,
        source_ref=stored_source_ref,
        kb_hit={
            "source": "knowledge",
            "provider": "trustguard-rag-mcp",
            "id": chunk_id,
            "score": hit.score,
            "snippet": hit.snippet[:500],
            "artifact_ref": resource.source_uri or "",
            "chunk_id": chunk_id,
            "skill_id": str(metadata.get("skill_id") or "").strip(),
            "workspace_id": tenant_id or "",
            "tenant_id": tenant_id or "",
            "title": resource.title or hit.title or "",
            "source_type": resource.source_type,
            "content_type": str(metadata.get("content_type") or "").strip(),
            "knowledge_scope": search_scope,
            "untrusted": True,
        },
    )


def _validate_resource(
    resource: KnowledgeResource,
    *,
    hit: KnowledgeHit,
    search_scope: str,
) -> None:
    if resource.scope != search_scope:
        raise KnowledgeMaterializationError("knowledge resource scope mismatch")
    if resource.resource_ref != hit.resource_ref:
        raise KnowledgeMaterializationError("knowledge resource reference mismatch")
    if resource.source_revision != hit.source_revision:
        raise KnowledgeMaterializationError("knowledge resource source revision mismatch")
    if resource.content_hash != hit.content_hash:
        raise KnowledgeMaterializationError("knowledge resource content hash mismatch")
    if resource.source_type != hit.source_type:
        raise KnowledgeMaterializationError("knowledge resource source type mismatch")
    if resource.visibility != hit.visibility:
        raise KnowledgeMaterializationError("knowledge resource visibility mismatch")
    if resource.document_id != hit.document_id:
        raise KnowledgeMaterializationError("knowledge resource document id mismatch")


def _chunk_id(source_ref: KnowledgeSourceRef) -> str:
    seed = "\x00".join(
        (
            source_ref.scope,
            source_ref.resource_ref,
            str(source_ref.source_revision),
            source_ref.content_hash,
        )
    )
    return f"chk-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def _write_idempotent(
    task_id: str,
    chunk_id: str,
    body: dict[str, Any],
    tenant_id: str | None,
) -> dict[str, Any]:
    existing = read_chunk(
        task_id,
        chunk_id,
        expect_tenant_id=tenant_id,
        deny_tenant_mismatch=True,
        require_tenant_when_bound=tenant_id is not None,
    )
    if existing is not None:
        _validate_existing(existing, body)
        return existing["content"]
    try:
        write_chunk(
            task_id,
            chunk_type="rag_knowledge",
            body=body,
            tenant_id=tenant_id,
            chunk_id=chunk_id,
            retention="ephemeral",
        )
    except ChunkStoreError as exc:
        if exc.code != "CHUNK_ID_COLLISION":
            raise
        existing = read_chunk(
            task_id,
            chunk_id,
            expect_tenant_id=tenant_id,
            deny_tenant_mismatch=True,
            require_tenant_when_bound=tenant_id is not None,
        )
        if existing is None:
            raise
        _validate_existing(existing, body)
        return existing["content"]
    return body


def _validate_existing(existing: dict[str, Any], expected_body: dict[str, Any]) -> None:
    actual_body = existing.get("content")
    if not isinstance(actual_body, dict) or _stable_body(actual_body) != _stable_body(
        expected_body
    ):
        raise KnowledgeMaterializationError(
            "task-local knowledge chunk conflicts with the requested source"
        )


def _stable_body(body: dict[str, Any]) -> dict[str, Any]:
    stable = dict(body)
    source_ref = stable.get("source_ref")
    if isinstance(source_ref, dict):
        stable["source_ref"] = {
            key: value for key, value in source_ref.items() if key != "retrieved_at"
        }
    return stable
