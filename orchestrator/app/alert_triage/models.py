"""告警研判 Agent 结构化数据模型。

所有输出必须经过 Pydantic 校验，禁止直接保存未约束的 LLM 文本。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class AlertVerdict(str, Enum):
    """研判结论枚举。"""

    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    SUSPICIOUS = "suspicious"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ExecutionLevel(str, Enum):
    """建议动作的执行权限等级。"""

    AUTO = "auto"
    MANUAL_CONFIRM = "manual_confirm"
    FORBIDDEN = "forbidden"


class TokenUsage(BaseModel):
    """LLM token 用量统计。"""

    input_tokens: int = Field(default=0, ge=0, description="输入 token 数")
    output_tokens: int = Field(default=0, ge=0, description="输出 token 数")


class RecommendedAction(BaseModel):
    """建议的后续动作。

    execution_level 明确区分：
    - auto: 可自动执行（MVP 默认不产生此类建议）
    - manual_confirm: 需要人工确认
    - forbidden: 禁止执行
    """

    action: str = Field(..., min_length=1, description="动作描述")
    rationale: str = Field(default="", description="动作理由")
    execution_level: ExecutionLevel = Field(
        default=ExecutionLevel.MANUAL_CONFIRM, description="执行权限等级"
    )


class XdrEvidenceRef(BaseModel):
    """XDR 证据引用。"""

    source: str = Field(..., description="证据来源（alert / incident / asset / proof）")
    uuid: str = Field(..., description="实体 UUID")
    field: str = Field(default="", description="引用的字段路径")
    value_summary: str = Field(default="", description="证据值摘要")


class RagCitation(BaseModel):
    """RAG 知识引用。"""

    chunk_id: str = Field(..., description="检索块标识")
    content_snippet: str = Field(default="", description="引文片段")
    source: str = Field(default="", description="知识来源（CVE / ATT&CK / 漏洞库等）")
    resource_uri: str = Field(default="", description="RAG MCP 资源 URI")
    source_uri: str | None = Field(default=None, description="原始知识来源 URI")
    page_no: int | None = Field(default=None, ge=1, description="来源页码")
    score: float | None = Field(default=None, description="检索相关性分数")
    source_revision: int | None = Field(default=None, ge=1, description="资源版本")
    content_hash: str = Field(default="", description="资源内容哈希")


class AlertTriageResult(BaseModel):
    """告警研判完整结果。"""

    alert_uuid: str = Field(..., min_length=1, description="XDR 告警 UUID")
    verdict: AlertVerdict = Field(..., description="研判结论")
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="置信度 (0.0-1.0)；证据不足时不应超过 0.5",
    )
    severity: str = Field(
        default="medium",
        pattern=r"^(critical|high|medium|low|info)$",
        description="告警严重程度",
    )
    summary: str = Field(default="", description="研判摘要")
    reasoning: str = Field(default="", description="研判推理过程")
    xdr_evidence_refs: list[XdrEvidenceRef] = Field(
        default_factory=list, description="XDR 原始证据引用列表"
    )
    rag_citations: list[RagCitation] = Field(
        default_factory=list, description="RAG 知识引用列表"
    )
    matched_whitelists: list[str] = Field(
        default_factory=list, description="匹配到的白名单规则标识"
    )
    related_incidents: list[str] = Field(
        default_factory=list, description="关联事件 UUID 列表"
    )
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list, description="建议动作列表"
    )
    missing_evidence: list[str] = Field(
        default_factory=list, description="阻断研判的核心缺失证据项"
    )
    enrichment_evidence: list[str] = Field(
        default_factory=list, description="不阻断当前结论、建议后续补充的增强证据项"
    )
    warnings: list[str] = Field(
        default_factory=list, description="研判过程中的警告（如降级信息）"
    )
    model: str = Field(default="", description="使用的 LLM 模型 ID")
    token_usage: TokenUsage = Field(default_factory=TokenUsage, description="Token 用量")
    started_at: str = Field(default="", description="研判开始时间 (ISO 8601)")
    finished_at: str = Field(default="", description="研判结束时间 (ISO 8601)")

    @field_validator("confidence")
    @classmethod
    def _check_confidence_vs_verdict(cls, v: float, info: Any) -> float:
        verdict = info.data.get("verdict") if hasattr(info, "data") else None
        if verdict == AlertVerdict.INSUFFICIENT_EVIDENCE and v > 0.5:
            raise ValueError(
                "confidence must be <= 0.5 when verdict is insufficient_evidence"
            )
        return v
