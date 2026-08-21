from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KnowledgeScopeName = Literal[
    "penetration",
    "alert-triage",
    "compliance",
    "product-docs",
    "threat-intelligence",
    "response-playbooks",
]


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


class KnowledgeScopeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_ids: list[str] = Field(min_length=1, max_length=16)
    default_mode: Literal[
        "auto",
        "focused",
        "comprehensive",
        "enumeration",
    ] = "auto"
    per_knowledge_base_limit: int = Field(default=20, ge=1, le=100)
    allowed_content_types: list[str] = Field(default_factory=list, max_length=100)
    allowed_workflow_types: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("knowledge_base_ids")
    @classmethod
    def normalize_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        normalized = list(
            dict.fromkeys(item.strip() for item in values if item.strip())
        )
        if not normalized:
            raise ValueError("knowledge_base_ids cannot be empty")
        return normalized

    @field_validator("allowed_content_types", "allowed_workflow_types")
    @classmethod
    def normalize_allowlists(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))


class ExperienceEvidenceRefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["task_chunk", "artifact", "trace"]
    ref: str = Field(min_length=1, max_length=512)


class ExperienceUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["trustguard-experience-upsert-v1"]
    external_id: str = Field(min_length=1, max_length=128)
    source_system: str = Field(min_length=1, max_length=64)
    source_revision: int = Field(ge=1)
    knowledge_scope: Literal["penetration", "alert-triage"]
    workflow_type: Literal["penetration", "alert-triage"]
    experience_type: str = Field(min_length=1, max_length=64)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=128)
    visibility: Literal["global", "workspace"]
    conditions: dict[str, Any] = Field(default_factory=dict)
    action_summary: str = Field(min_length=1, max_length=8000)
    outcome_summary: str = Field(min_length=1, max_length=8000)
    skill_id: str | None = Field(default=None, max_length=128)
    phase: str | None = Field(default=None, max_length=64)
    source_task_id: str | None = Field(default=None, max_length=128)
    evidence_refs: list[ExperienceEvidenceRefRequest] = Field(
        default_factory=list,
        max_length=20,
    )
    expires_at: datetime | None = None

    @field_validator("conditions")
    @classmethod
    def limit_conditions(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 50:
            raise ValueError("conditions may have at most 50 properties")
        return value

    @model_validator(mode="after")
    def validate_identity_and_scope(self) -> "ExperienceUpsertRequest":
        if self.workflow_type != self.knowledge_scope:
            raise ValueError("workflow_type and knowledge_scope must match")
        if self.visibility == "workspace" and not self.workspace_id:
            raise ValueError("workspace visibility requires workspace_id")
        if self.visibility == "global" and self.workspace_id is not None:
            raise ValueError("global visibility requires a null workspace_id")
        return self


class ExperienceFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["trustguard-experience-feedback-v1"]
    event_id: str = Field(min_length=1, max_length=128)
    experience_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    workflow_type: Literal["penetration", "alert-triage"]
    outcome: Literal["success", "failure", "neutral"]
    evidence_level: Literal["reported", "observed", "verified"]
    notes: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None


class ExperienceStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["candidate", "pending", "proven", "deprecated", "archived"]
    reason: str | None = Field(default=None, max_length=512)


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
    schedule_enabled: bool = False
    schedule_interval_minutes: int | None = Field(
        default=None,
        ge=5,
        le=525_600,
    )

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
        if self.schedule_enabled and self.schedule_interval_minutes is None:
            raise ValueError("周期采集必须设置采集周期")
        return self


class KnowledgeCrawlerReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    item_ids: list[str] = Field(min_length=1, max_length=200)
