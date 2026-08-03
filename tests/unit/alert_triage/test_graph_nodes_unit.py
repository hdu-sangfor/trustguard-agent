"""告警研判图节点单元测试。

覆盖：各节点状态转换、失败路径、证据不足处理。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator" / "app"))

from unittest.mock import AsyncMock, patch

import pytest

from alert_triage.graph_core import (
    TriageState,
    load_alert,
    collect_evidence,
    check_whitelist,
    query_rag,
    make_decision,
    validate_decision,
    persist_result,
    recommend_action,
    route_after_load,
)


def _base_state(**overrides) -> TriageState:
    state: TriageState = {
        "task_id": "at-test-1",
        "alert_uuid": "alert-123",
        "scenario_id": None,
        "enable_rag": True,
        "caller_notes": None,
        "alert": None,
        "alert_proof": None,
        "assets": [],
        "related_incidents": [],
        "incident_proofs": [],
        "whitelist_matches": [],
        "rag_response": None,
        "rag_degraded": False,
        "raw_decision": None,
        "validation_errors": [],
        "result": None,
        "status": "RUNNING",
        "error": None,
        "warnings": [],
        "missing_evidence": [],
        "started_at": "2026-07-23T10:00:00Z",
        "finished_at": "",
        "current_node": "init",
        "trace_events": [],
    }
    state.update(overrides)
    return state


class TestLoadAlert:
    @pytest.mark.asyncio
    async def test_alert_found(self):
        mock_alert = {"uuid": "alert-123", "alert_type": "malware", "severity": "high"}
        state = _base_state()
        with patch("clients.xdr_client.get_alert", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_alert
            result = await load_alert(state)
            assert result["status"] == "RUNNING"
            assert result["alert"] == mock_alert
            assert result["current_node"] == "load_alert"

    @pytest.mark.asyncio
    async def test_alert_not_found(self):
        state = _base_state()
        with patch("clients.xdr_client.get_alert", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {}
            result = await load_alert(state)
            assert result["status"] == "FAILED"
            assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_xdr_unavailable(self):
        state = _base_state()
        from clients.xdr_client import XDRClientError

        with patch("clients.xdr_client.get_alert", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = XDRClientError("timeout")
            result = await load_alert(state)
            assert result["status"] == "FAILED"
            assert "XDR unavailable" in result.get("error", "")


class TestCollectEvidence:
    @pytest.mark.asyncio
    async def test_collect_all(self):
        state = _base_state(alert={"uuid": "alert-123", "hostname": "win-pc"})
        with patch("clients.xdr_client.get_alert_proof", new_callable=AsyncMock) as mock_proof, \
             patch("clients.xdr_client.get_assets", new_callable=AsyncMock) as mock_assets, \
             patch("clients.xdr_client.list_incidents", new_callable=AsyncMock) as mock_incidents, \
             patch("clients.xdr_client.get_incident_proof", new_callable=AsyncMock) as mock_iproof:
            mock_proof.return_value = {"cmd": "powershell"}
            mock_assets.return_value = [{"hostname": "win-pc"}]
            mock_incidents.return_value = [{"uuid": "inc-1"}]
            mock_iproof.return_value = {"note": "test"}
            result = await collect_evidence(state)
            assert result["status"] == "RUNNING"
            assert result["alert_proof"] == {"cmd": "powershell"}
            assert len(result["assets"]) == 1
            assert len(result["related_incidents"]) == 1

    @pytest.mark.asyncio
    async def test_proof_missing_adds_to_missing_evidence(self):
        state = _base_state(alert={"uuid": "alert-123", "hostname": "win-pc"})
        from clients.xdr_client import XDRClientError

        with patch("clients.xdr_client.get_alert_proof", new_callable=AsyncMock) as mock_proof, \
             patch("clients.xdr_client.get_assets", new_callable=AsyncMock) as mock_assets, \
             patch("clients.xdr_client.list_incidents", new_callable=AsyncMock) as mock_incidents:
            mock_proof.side_effect = XDRClientError("not found")
            mock_assets.return_value = []
            mock_incidents.return_value = []
            result = await collect_evidence(state)
            assert "alert_proof" in result["missing_evidence"]


class TestCheckWhitelist:
    @pytest.mark.asyncio
    async def test_whitelist_match(self):
        state = _base_state(alert={"command_line": "powershell -enc ..."})
        with patch("clients.xdr_client.match_whitelist", new_callable=AsyncMock) as mock_wl:
            mock_wl.return_value = [{"id": "wl-1", "rule_id": "r1"}]
            result = await check_whitelist(state)
            assert len(result["whitelist_matches"]) == 1

    @pytest.mark.asyncio
    async def test_whitelist_unavailable_graceful(self):
        state = _base_state()
        from clients.xdr_client import XDRClientError

        with patch("clients.xdr_client.match_whitelist", new_callable=AsyncMock) as mock_wl:
            mock_wl.side_effect = XDRClientError("unavailable")
            result = await check_whitelist(state)
            assert "whitelist_check_unavailable" in result["warnings"]


class TestQueryRAG:
    @pytest.mark.asyncio
    async def test_rag_disabled(self):
        state = _base_state(enable_rag=False)
        result = await query_rag(state)
        assert result["rag_degraded"] is True
        assert "RAG_DISABLED_BY_REQUEST" in result["warnings"]

    @pytest.mark.asyncio
    async def test_rag_degraded(self):
        state = _base_state(enable_rag=True, alert={"alert_type": "malware"})
        # RAG client is already a placeholder that returns degraded
        result = await query_rag(state)
        assert result["rag_degraded"] is True
        assert "RAG_DEGRADED" in result["warnings"]


class TestValidateDecision:
    @pytest.mark.asyncio
    async def test_valid_decision(self):
        state = _base_state(raw_decision={
            "verdict": "true_positive",
            "confidence": 0.9,
            "severity": "high",
            "summary": "malware beacon detected",
            "reasoning": "...",
            "recommended_actions": [{"action": "isolate", "execution_level": "manual_confirm"}],
        })
        result = await validate_decision(state)
        assert len(result["validation_errors"]) == 0

    @pytest.mark.asyncio
    async def test_insufficient_evidence_high_confidence_capped(self):
        state = _base_state(
            raw_decision={"verdict": "insufficient_evidence", "confidence": 0.9},
            missing_evidence=["alert_proof"],
        )
        result = await validate_decision(state)
        assert result["raw_decision"]["confidence"] == 0.4

    @pytest.mark.asyncio
    async def test_deterministic_verdict_with_missing_evidence(self):
        state = _base_state(
            raw_decision={"verdict": "true_positive", "confidence": 0.7, "severity": "high"},
            missing_evidence=["alert_proof"],
        )
        result = await validate_decision(state)
        assert result["raw_decision"]["verdict"] == "suspicious"

    @pytest.mark.asyncio
    async def test_auto_action_demoted(self):
        state = _base_state(raw_decision={
            "verdict": "suspicious",
            "confidence": 0.5,
            "severity": "medium",
            "recommended_actions": [{"action": "block ip", "execution_level": "auto"}],
        })
        result = await validate_decision(state)
        actions = result["raw_decision"]["recommended_actions"]
        assert actions[0]["execution_level"] == "manual_confirm"

    @pytest.mark.asyncio
    async def test_non_numeric_confidence_is_degraded_instead_of_crashing(self):
        state = _base_state(raw_decision={
            "verdict": "suspicious",
            "confidence": "high",
            "severity": "medium",
        })
        result = await validate_decision(state)
        assert result["raw_decision"]["confidence"] == 0.3
        assert any("invalid confidence type" in error for error in result["validation_errors"])


class TestRouting:
    def test_route_after_load_failed(self):
        state = _base_state(status="FAILED")
        assert route_after_load(state) == "end"

    def test_route_after_load_ok(self):
        state = _base_state(status="RUNNING")
        assert route_after_load(state) == "collect_evidence"
