from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeScope(StrEnum):
    PENETRATION = "penetration"
    ALERT_TRIAGE = "alert-triage"
    COMPLIANCE = "compliance"
    PRODUCT_DOCS = "product-docs"
    THREAT_INTELLIGENCE = "threat-intelligence"
    RESPONSE_PLAYBOOKS = "response-playbooks"


class RetrievalMode(StrEnum):
    AUTO = "auto"
    FOCUSED = "focused"
    COMPREHENSIVE = "comprehensive"
    ENUMERATION = "enumeration"


class KnowledgeSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_types: list[str] = Field(default_factory=list, max_length=20)
    source_types: list[Literal["document", "experience", "playbook"]] = Field(
        default_factory=list,
        max_length=3,
    )


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["trustguard-knowledge-search-request-v1"] = (
        "trustguard-knowledge-search-request-v1"
    )
    query: str = Field(min_length=1, max_length=2000)
    scope: KnowledgeScope
    mode: RetrievalMode = RetrievalMode.AUTO
    limit: int = Field(default=5, ge=1, le=20)
    rewrite: bool = False
    filters: KnowledgeSearchFilters = Field(default_factory=KnowledgeSearchFilters)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("query cannot be blank")
        return normalized


class KnowledgeHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_uri: str = Field(pattern=r"^trustguard-rag://")
    resource_ref: str = Field(min_length=1, max_length=2048, pattern=r"^krf1\.")
    source_revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    snippet: str = Field(max_length=4000)
    score: float
    title: str | None = Field(default=None, max_length=512)
    document_id: str | None = Field(default=None, max_length=128)
    filename: str | None = Field(default=None, max_length=512)
    page_no: int | None = Field(default=None, ge=1)
    source_uri: str | None = Field(default=None, max_length=2048)
    source_type: Literal["document", "experience", "playbook"]
    workflow_type: str | None = Field(default=None, max_length=64)
    effectiveness: (
        Literal["unknown", "promising", "effective", "ineffective"] | None
    ) = None
    visibility: Literal["global", "workspace"]
    expanded: bool


class McpQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: RetrievalMode
    source: Literal["explicit", "heuristic", "llm"]


class KnowledgeCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "partial", "not_applicable", "unknown"]
    warning: str | None = Field(default=None, max_length=1000)


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["trustguard-knowledge-search-v1"]
    request_id: str = Field(min_length=1, max_length=128)
    scope: str = Field(min_length=1, max_length=64)
    status: Literal["ok", "degraded"]
    content_revision: str = Field(min_length=1, max_length=128)
    hits: list[KnowledgeHit] = Field(max_length=20)
    query_plan: McpQueryPlan
    coverage: KnowledgeCoverage
    degraded_components: list[
        Literal["vector", "keyword", "rerank", "rewrite", "federation"]
    ]
    latency_ms: float = Field(ge=0)


class KnowledgeResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["trustguard-knowledge-resource-v1"]
    scope: str = Field(min_length=1, max_length=64)
    content_revision: str = Field(min_length=1, max_length=128)
    resource_ref: str = Field(min_length=1, max_length=2048, pattern=r"^krf1\.")
    source_revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    document_id: str | None = Field(default=None, max_length=128)
    experience_id: str | None = Field(default=None, max_length=128)
    text: str = Field(min_length=1, max_length=32000)
    title: str | None = Field(default=None, max_length=512)
    filename: str | None = Field(default=None, max_length=512)
    page_no: int | None = Field(default=None, ge=1)
    source_uri: str | None = Field(default=None, max_length=2048)
    source_type: Literal["document", "experience", "playbook"]
    workflow_type: str | None = Field(default=None, max_length=64)
    effectiveness: (
        Literal["unknown", "promising", "effective", "ineffective"] | None
    ) = None
    visibility: Literal["global", "workspace"]
    metadata: dict[str, Any]
