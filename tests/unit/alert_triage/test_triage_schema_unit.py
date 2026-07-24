"""研判结果 Schema 单元测试。

覆盖：合法/非法字段、边界值、confidence vs verdict 校验。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator" / "app"))

import pytest
from pydantic import ValidationError

from alert_triage.models import (
    AlertTriageResult,
    AlertVerdict,
    ExecutionLevel,
    RecommendedAction,
    TokenUsage,
    XdrEvidenceRef,
    RagCitation,
)


class TestAlertVerdict:
    def test_valid_verdicts(self):
        assert AlertVerdict.TRUE_POSITIVE.value == "true_positive"
        assert AlertVerdict.FALSE_POSITIVE.value == "false_positive"
        assert AlertVerdict.SUSPICIOUS.value == "suspicious"
        assert AlertVerdict.INSUFFICIENT_EVIDENCE.value == "insufficient_evidence"

    def test_verdict_from_string(self):
        assert AlertVerdict("true_positive") == AlertVerdict.TRUE_POSITIVE
        assert AlertVerdict("false_positive") == AlertVerdict.FALSE_POSITIVE

    def test_invalid_verdict_raises(self):
        with pytest.raises(ValueError):
            AlertVerdict("unknown")


class TestTokenUsage:
    def test_defaults(self):
        tu = TokenUsage()
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0

    def test_negative_tokens_rejected(self):
        with pytest.raises(ValidationError):
            TokenUsage(input_tokens=-1)
        with pytest.raises(ValidationError):
            TokenUsage(output_tokens=-1)


class TestRecommendedAction:
    def test_valid_action(self):
        ra = RecommendedAction(action="人工确认后封禁IP", rationale="IP被确认为C2节点")
        assert ra.action == "人工确认后封禁IP"
        assert ra.execution_level == ExecutionLevel.MANUAL_CONFIRM

    def test_empty_action_rejected(self):
        with pytest.raises(ValidationError):
            RecommendedAction(action="")

    def test_auto_level(self):
        ra = RecommendedAction(action="忽略", execution_level=ExecutionLevel.AUTO)
        assert ra.execution_level == ExecutionLevel.AUTO

    def test_forbidden_level(self):
        ra = RecommendedAction(action="删除系统文件", execution_level=ExecutionLevel.FORBIDDEN)
        assert ra.execution_level == ExecutionLevel.FORBIDDEN


class TestAlertTriageResult:
    def test_minimal_valid_result(self):
        result = AlertTriageResult(
            alert_uuid="alert-123",
            verdict=AlertVerdict.INSUFFICIENT_EVIDENCE,
            confidence=0.3,
        )
        assert result.alert_uuid == "alert-123"
        assert result.verdict == AlertVerdict.INSUFFICIENT_EVIDENCE
        assert result.confidence == 0.3
        assert result.severity == "medium"
        assert result.recommended_actions == []

    def test_full_result(self):
        refs = [XdrEvidenceRef(source="alert", uuid="alert-123", field="cmd", value_summary="powershell -enc ...")]
        rags = [RagCitation(chunk_id="chunk-1", content_snippet="powershell encoding常见于...", source="ATT&CK T1059.001")]
        actions = [RecommendedAction(action="人工确认是否为资产盘点", execution_level=ExecutionLevel.MANUAL_CONFIRM)]
        result = AlertTriageResult(
            alert_uuid="alert-123",
            verdict=AlertVerdict.FALSE_POSITIVE,
            confidence=0.85,
            severity="medium",
            summary="已审批的PowerShell资产盘点脚本",
            reasoning="该命令行hash匹配白名单...",
            xdr_evidence_refs=refs,
            rag_citations=rags,
            matched_whitelists=["wl-powershell-asset"],
            related_incidents=[],
            recommended_actions=actions,
            missing_evidence=[],
            warnings=[],
            model="gpt-4o",
            token_usage=TokenUsage(input_tokens=1200, output_tokens=300),
            started_at="2026-07-23T10:00:00Z",
            finished_at="2026-07-23T10:00:30Z",
        )
        assert result.verdict == AlertVerdict.FALSE_POSITIVE
        assert len(result.xdr_evidence_refs) == 1
        assert len(result.rag_citations) == 1
        assert len(result.recommended_actions) == 1

    def test_insufficient_evidence_high_confidence_rejected(self):
        with pytest.raises(ValidationError, match="insufficient_evidence"):
            AlertTriageResult(
                alert_uuid="alert-123",
                verdict=AlertVerdict.INSUFFICIENT_EVIDENCE,
                confidence=0.9,
            )

    def test_true_positive_high_confidence_allowed(self):
        result = AlertTriageResult(
            alert_uuid="alert-123",
            verdict=AlertVerdict.TRUE_POSITIVE,
            confidence=0.95,
        )
        assert result.verdict == AlertVerdict.TRUE_POSITIVE

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValidationError, match="severity"):
            AlertTriageResult(
                alert_uuid="alert-123",
                verdict=AlertVerdict.SUSPICIOUS,
                severity="unknown",
            )

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            AlertTriageResult(
                alert_uuid="alert-123",
                verdict=AlertVerdict.SUSPICIOUS,
                confidence=1.5,
            )
        with pytest.raises(ValidationError):
            AlertTriageResult(
                alert_uuid="alert-123",
                verdict=AlertVerdict.SUSPICIOUS,
                confidence=-0.1,
            )

    def test_empty_alert_uuid_rejected(self):
        with pytest.raises(ValidationError):
            AlertTriageResult(
                alert_uuid="",
                verdict=AlertVerdict.SUSPICIOUS,
            )
