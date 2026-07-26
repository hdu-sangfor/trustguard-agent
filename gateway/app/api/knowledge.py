from __future__ import annotations

import os
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.audit import record_audit
from app.clients.rag_client import rag_client
from app.responses import ok
from app.schemas.knowledge import (
    IngestConflictResolveRequest,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseUpdateRequest,
    KnowledgeDocumentUpdateRequest,
    RagAnswerRequest,
    RagSearchRequest,
)
from app.security.auth import CurrentUser, require_roles

RAG_UPLOAD_MAX_BYTES = max(
    1,
    int(os.getenv("RAG_UPLOAD_MAX_BYTES", "52428800")),
)

KnowledgeReader = Annotated[
    CurrentUser,
    Depends(require_roles("ADMIN", "OPERATOR", "VIEWER")),
]
KnowledgeManager = Annotated[
    CurrentUser,
    Depends(require_roles("ADMIN", "OPERATOR")),
]

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _safe_upload_filename(filename: str) -> str:
    normalized = filename.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or "\x00" in normalized
    ):
        raise HTTPException(status_code=422, detail="文件名无效")
    return normalized


async def _get_scoped_document(
    document_id: str,
    knowledge_base_id: str,
) -> dict[str, Any]:
    document = await rag_client.request(
        "GET",
        f"/v1/documents/{quote(document_id, safe='')}",
        timeout=10.0,
    )
    if (
        not isinstance(document, dict)
        or document.get("knowledge_base_id") != knowledge_base_id
    ):
        raise HTTPException(status_code=404, detail="文档不属于当前知识库")
    return document


async def _get_scoped_ingest_job(
    job_id: str,
    knowledge_base_id: str,
) -> dict[str, Any]:
    job = await rag_client.request(
        "GET",
        f"/v1/ingest/jobs/{quote(job_id, safe='')}",
        timeout=10.0,
    )
    if (
        not isinstance(job, dict)
        or job.get("knowledge_base_id") != knowledge_base_id
    ):
        raise HTTPException(status_code=404, detail="入库任务不属于当前知识库")
    return job


@router.get("/health")
async def knowledge_health(_user: KnowledgeReader) -> dict[str, Any]:
    return ok(await rag_client.request("GET", "/health", timeout=6.0))


@router.get("/bases")
async def knowledge_bases(_user: KnowledgeReader) -> dict[str, Any]:
    return ok(
        await rag_client.request(
            "GET",
            "/v1/knowledge-bases",
            timeout=10.0,
        )
    )


@router.post("/bases")
async def create_knowledge_base(
    request: KnowledgeBaseCreateRequest,
    actor: KnowledgeManager,
) -> dict[str, Any]:
    result = await rag_client.request(
        "POST",
        "/v1/knowledge-bases",
        json_body=request.model_dump(),
        timeout=15.0,
    )
    knowledge_base_id = (
        str(result.get("id") or "")
        if isinstance(result, dict)
        else ""
    )
    record_audit(
        "KNOWLEDGE_BASE_CREATED",
        actor.username,
        knowledge_base_id,
        request.name,
    )
    return ok(result)


@router.patch("/bases/{knowledge_base_id}")
async def update_knowledge_base(
    knowledge_base_id: str,
    request: KnowledgeBaseUpdateRequest,
    actor: KnowledgeManager,
) -> dict[str, Any]:
    if not request.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail="至少提供一个可更新字段",
        )
    result = await rag_client.request(
        "PATCH",
        f"/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}",
        json_body=request.model_dump(exclude_unset=True),
        timeout=15.0,
    )
    record_audit(
        "KNOWLEDGE_BASE_UPDATED",
        actor.username,
        knowledge_base_id,
    )
    return ok(result)


@router.delete("/bases/{knowledge_base_id}")
async def delete_knowledge_base(
    knowledge_base_id: str,
    actor: KnowledgeManager,
) -> dict[str, Any]:
    await rag_client.request(
        "DELETE",
        f"/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}",
        timeout=15.0,
    )
    record_audit(
        "KNOWLEDGE_BASE_DELETED",
        actor.username,
        knowledge_base_id,
    )
    return ok({"knowledgeBaseId": knowledge_base_id, "deleted": True})


@router.get("/capabilities")
async def knowledge_capabilities(
    _user: KnowledgeReader,
) -> dict[str, Any]:
    result = await rag_client.request(
        "GET",
        "/v1/sources/capabilities",
        timeout=10.0,
    )
    if isinstance(result, dict):
        result = dict(result)
        result["gateway"] = {"max_upload_bytes": RAG_UPLOAD_MAX_BYTES}
        sources = result.get("sources")
        if isinstance(sources, list):
            normalized_sources: list[Any] = []
            for source in sources:
                if not isinstance(source, dict):
                    normalized_sources.append(source)
                    continue
                normalized = dict(source)
                upstream_max = normalized.get("max_bytes")
                if isinstance(upstream_max, int) and upstream_max > 0:
                    normalized["max_bytes"] = min(
                        upstream_max,
                        RAG_UPLOAD_MAX_BYTES,
                    )
                else:
                    normalized["max_bytes"] = RAG_UPLOAD_MAX_BYTES
                normalized_sources.append(normalized)
            result["sources"] = normalized_sources
    return ok(result)


@router.get("/documents")
async def knowledge_documents(
    _user: KnowledgeReader,
    knowledge_base_id: str = Query(min_length=1, max_length=36),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None, max_length=32),
    query: str | None = Query(default=None, max_length=512),
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "knowledge_base_id": knowledge_base_id,
        "offset": offset,
        "limit": limit,
    }
    if status:
        params["status"] = status
    if query and query.strip():
        params["q"] = query.strip()
    return ok(
        await rag_client.request(
            "GET",
            "/v1/documents",
            params=params,
            timeout=10.0,
        )
    )


@router.get("/documents/{document_id}")
async def knowledge_document(
    document_id: str,
    _user: KnowledgeReader,
    knowledge_base_id: str = Query(min_length=1, max_length=36),
) -> dict[str, Any]:
    return ok(await _get_scoped_document(document_id, knowledge_base_id))


@router.get("/documents/{document_id}/chunks")
async def knowledge_document_chunks(
    document_id: str,
    _user: KnowledgeReader,
    knowledge_base_id: str = Query(min_length=1, max_length=36),
) -> dict[str, Any]:
    await _get_scoped_document(document_id, knowledge_base_id)
    return ok(
        await rag_client.request(
            "GET",
            f"/v1/documents/{quote(document_id, safe='')}/chunks",
            timeout=15.0,
        )
    )


@router.patch("/documents/{document_id}")
async def update_knowledge_document(
    document_id: str,
    request: KnowledgeDocumentUpdateRequest,
    actor: KnowledgeManager,
    knowledge_base_id: str = Query(min_length=1, max_length=36),
) -> dict[str, Any]:
    await _get_scoped_document(document_id, knowledge_base_id)
    if not request.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail="至少提供一个可更新字段",
        )
    document = await rag_client.request(
        "PATCH",
        f"/v1/documents/{quote(document_id, safe='')}",
        json_body=request.model_dump(exclude_unset=True),
        timeout=15.0,
    )
    if (
        not isinstance(document, dict)
        or document.get("knowledge_base_id") != knowledge_base_id
    ):
        raise HTTPException(status_code=404, detail="文档不属于当前知识库")
    record_audit(
        "KNOWLEDGE_DOCUMENT_UPDATED",
        actor.username,
        document_id,
        knowledge_base_id,
    )
    return ok(document)


@router.delete("/documents/{document_id}")
async def delete_knowledge_document(
    document_id: str,
    actor: KnowledgeManager,
    knowledge_base_id: str = Query(min_length=1, max_length=36),
) -> dict[str, Any]:
    await _get_scoped_document(document_id, knowledge_base_id)
    await rag_client.request(
        "DELETE",
        f"/v1/documents/{quote(document_id, safe='')}",
        timeout=20.0,
    )
    record_audit(
        "KNOWLEDGE_DOCUMENT_DELETED",
        actor.username,
        document_id,
        knowledge_base_id,
    )
    return ok({"documentId": document_id, "deleted": True})


@router.post("/bases/{knowledge_base_id}/documents")
async def upload_knowledge_document(
    knowledge_base_id: str,
    request: Request,
    actor: KnowledgeManager,
    filename: str = Query(min_length=1, max_length=512),
) -> dict[str, Any]:
    safe_filename = _safe_upload_filename(filename)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > RAG_UPLOAD_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="上传文件超过 Agent 允许的大小",
                )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Content-Length 无效",
            )
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="上传文件不能为空")
    if len(body) > RAG_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="上传文件超过 Agent 允许的大小",
        )

    await rag_client.request(
        "GET",
        f"/v1/knowledge-bases/{quote(knowledge_base_id, safe='')}",
        timeout=10.0,
    )
    mime_type = (
        request.headers.get("content-type")
        or "application/octet-stream"
    ).split(";", 1)[0].strip()
    result = await rag_client.request(
        "POST",
        "/v1/ingest/jobs",
        data={
            "source_type": "file",
            "knowledge_base_id": knowledge_base_id,
        },
        files={
            "file": (
                safe_filename,
                body,
                mime_type or "application/octet-stream",
            )
        },
        timeout=120.0,
    )
    job_id = (
        str(result.get("job_id") or "")
        if isinstance(result, dict)
        else ""
    )
    record_audit(
        "KNOWLEDGE_DOCUMENT_UPLOADED",
        actor.username,
        job_id,
        f"{knowledge_base_id}:{safe_filename}",
    )
    return ok(result)


@router.get("/bases/{knowledge_base_id}/ingest/jobs/{job_id}")
async def get_knowledge_ingest_job(
    knowledge_base_id: str,
    job_id: str,
    _user: KnowledgeReader,
) -> dict[str, Any]:
    return ok(await _get_scoped_ingest_job(job_id, knowledge_base_id))


@router.post("/bases/{knowledge_base_id}/ingest/jobs/{job_id}/resolve")
async def resolve_knowledge_ingest_conflict(
    knowledge_base_id: str,
    job_id: str,
    request: IngestConflictResolveRequest,
    actor: KnowledgeManager,
) -> dict[str, Any]:
    await _get_scoped_ingest_job(job_id, knowledge_base_id)
    job = await rag_client.request(
        "POST",
        f"/v1/ingest/jobs/{quote(job_id, safe='')}/resolve",
        json_body=request.model_dump(),
        timeout=60.0,
    )
    if (
        not isinstance(job, dict)
        or job.get("knowledge_base_id") != knowledge_base_id
    ):
        raise HTTPException(
            status_code=404,
            detail="入库任务不属于当前知识库",
        )
    record_audit(
        "KNOWLEDGE_INGEST_CONFLICT_RESOLVED",
        actor.username,
        job_id,
        request.keep_document_id,
    )
    return ok(job)


@router.post("/search")
async def knowledge_search(
    request: RagSearchRequest,
    _user: KnowledgeReader,
) -> dict[str, Any]:
    if not request.enable_vector and not request.enable_keyword:
        raise HTTPException(
            status_code=422,
            detail="至少启用一种检索方式",
        )
    return ok(
        await rag_client.request(
            "POST",
            "/v1/search",
            json_body={
                **request.model_dump(),
                "enable_abstention": True,
                "require_exact_entity_match": True,
            },
            timeout=30.0,
        )
    )


@router.post("/answer")
async def knowledge_answer(
    request: RagAnswerRequest,
    _user: KnowledgeReader,
) -> dict[str, Any]:
    if not request.enable_vector and not request.enable_keyword:
        raise HTTPException(
            status_code=422,
            detail="至少启用一种检索方式",
        )
    return ok(
        await rag_client.request(
            "POST",
            "/v1/answer",
            json_body={
                **request.model_dump(),
                "enable_abstention": True,
                "require_exact_entity_match": True,
            },
            timeout=90.0,
        )
    )
