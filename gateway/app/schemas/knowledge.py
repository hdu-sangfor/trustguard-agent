from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class KnowledgeCrawlerCreateRequest(BaseModel):
    """Bounded crawler options exposed by the Agent BFF."""

    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = Field(min_length=1, max_length=36)
    preset_ids: list[str] = Field(default_factory=list, max_length=1)
    urls: list[str] = Field(default_factory=list, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    site_urls: list[str] = Field(default_factory=list, max_length=50)
    max_results_per_keyword: int = Field(default=10, ge=1, le=20)
    max_pages_per_site: int = Field(default=10, ge=1, le=50)
    max_total_pages: int = Field(default=100, ge=1, le=200)
    min_content_chars: int = Field(default=80, ge=0, le=10_000)
    fetch_delay_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_base_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    force: bool = False
    review_mode: Literal["human", "agent"] = "human"
    review_criteria: str = Field(default="", max_length=8_000)

    @model_validator(mode="after")
    def validate_collection_source(self) -> "KnowledgeCrawlerCreateRequest":
        allowed_presets = {
            f"agent_0{index}_{suffix}"
            for index, suffix in enumerate(
                (
                    "asset_fingerprint",
                    "vulnerability_weakness",
                    "detection_exploit_validation",
                    "remediation_closure",
                    "attack_chain",
                    "xdr_detection",
                    "tool_runbook",
                    "threat_intelligence",
                    "compliance_reporting",
                ),
                start=1,
            )
        }
        unknown = set(self.preset_ids) - allowed_presets
        if unknown:
            raise ValueError("仅允许使用 Agent 的 9 类知识库预置")
        if not any((self.preset_ids, self.urls, self.keywords, self.site_urls)):
            raise ValueError("至少提供一个分类预置、URL、关键词或站点入口")
        for values in (self.urls, self.keywords, self.site_urls):
            if any(not value.strip() for value in values):
                raise ValueError("采集来源不能包含空白项")
        if self.review_mode == "agent" and not self.review_criteria.strip() and not self.preset_ids:
            raise ValueError("Agent 审核必须填写审核标准")
        return self


class KnowledgeCrawlerReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    item_ids: list[str] = Field(min_length=1, max_length=200)
