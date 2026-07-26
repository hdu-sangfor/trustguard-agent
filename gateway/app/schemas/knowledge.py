from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RagSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    knowledge_base_id: str = Field(min_length=1, max_length=36)
    top_k: int = Field(default=8, ge=1, le=20)
    retrieval_mode: Literal[
        "auto",
        "focused",
        "comprehensive",
        "enumeration",
    ] = "auto"
    enable_query_rewrite: bool = False
    enable_vector: bool = True
    enable_keyword: bool = True
    enable_rerank: bool = True


class RagAnswerRequest(RagSearchRequest):
    """Single-turn grounded answer options exposed by the knowledge BFF."""


class KnowledgeBaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    embedding_profile: str = Field(
        default="configured",
        min_length=1,
        max_length=64,
    )


class KnowledgeBaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)


class KnowledgeDocumentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=512)
    original_filename: str | None = Field(default=None, max_length=512)
    metadata: dict[str, Any] | None = None


class IngestConflictResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep_document_id: str = Field(min_length=1, max_length=36)
