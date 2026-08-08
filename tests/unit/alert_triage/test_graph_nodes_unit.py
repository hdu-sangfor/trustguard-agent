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
    _redact_xdr_params,
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
        "enrichment_evidence": [],
        "started_at": "2026-07-23T10:00:00Z",
        "finished_at": "",
        "current_node": "init",
        "trace_events": [],
    }
    state.update(overrides)
    return state


def test_xdr_trace_params_are_redacted_and_bounded():
    raw_command = "powershell -Password top-secret -Token bearer-value"
    redacted = _redact_xdr_params({
        "command_line": raw_command,
        "password": "top-secret",
        "hostIp": "10.20.30.40",
        "hostIps": ["10.20.30.40", "2001:db8::1"],
        "uuIds": [f"log-{index}" for index in range(25)],
        "page": 1,
        "nested": {"Authorization": "Bearer abc", "note": "safe"},
    })

    encoded = str(redacted)
    assert raw_command not in encoded
    assert "top-secret" not in encoded
    assert "bearer-value" not in encoded
    assert redacted["command_line"].startswith("[REDACTED_COMMAND length=")
    assert redacted["password"] == "[REDACTED_SECRET]"
    assert redacted["hostIp"] == "10.20.30.*"
    assert redacted["hostIps"] == ["10.20.30.*", "2001:0db8:0000:0000:*"]
    assert redacted["page"] == 1
    assert len(redacted["uuIds"]) == 21
    assert redacted["uuIds"][-1] == "[TRUNCATED 5 ITEMS]"
    assert redacted["nested"]["Authorization"] == "[REDACTED_SECRET]"
    assert redacted["nested"]["note"] == "safe"


class TestLoadAlert:
    @pytest.mark.asyncio
    async def test_alert_found(self):
        mock_alert = {"uuid": "alert-123", "alert_type": "malware", "severity": "high"}
        state = _base_state()
        with patch("clients.xdr_client.get_alert", new_callable=AsyncMock) as mock_get, \
             patch("alert_triage.graph_core._emit", new_callable=AsyncMock) as mock_emit:
            mock_get.return_value = mock_alert
            result = await load_alert(state)
            assert result["status"] == "RUNNING"
            assert result["alert"] == mock_alert
            assert result["current_node"] == "load_alert"
            emitted = [(call.args[1], call.args[2]) for call in mock_emit.await_args_list]
            request = next(payload for event, payload in emitted if event == "AT_XDR_REQUEST")
            response = next(payload for event, payload in emitted if event == "AT_XDR_RESPONSE")
            assert request["operation"] == "读取告警详情"
            assert request["params"] == {"alert_uuid": "alert-123"}
            assert response["record_ids"] == ["alert-123"]

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
        state = _base_state(alert={
            "uuid": "alert-123",
            "hostname": "win-pc",
            "logIds": ["log-1"],
        })
        with patch("clients.xdr_client.get_alert_proof", new_callable=AsyncMock) as mock_proof, \
             patch("clients.xdr_client.list_endpoint_security_logs", new_callable=AsyncMock) as mock_logs, \
             patch("clients.xdr_client.get_assets", new_callable=AsyncMock) as mock_assets, \
             patch("clients.xdr_client.list_incidents", new_callable=AsyncMock) as mock_incidents, \
             patch("clients.xdr_client.get_incident_proof", new_callable=AsyncMock) as mock_iproof, \
             patch("alert_triage.graph_core._emit", new_callable=AsyncMock) as mock_emit:
            mock_proof.return_value = {"cmd": "powershell"}
            mock_logs.return_value = [{"uuId": "log-1"}]
            mock_assets.return_value = [{"hostname": "win-pc"}]
            mock_incidents.return_value = [{"uuid": "inc-1"}]
            mock_iproof.return_value = {"note": "test"}
            result = await collect_evidence(state)
            assert result["status"] == "RUNNING"
            assert result["alert_proof"] == {"cmd": "powershell"}
            assert len(result["assets"]) == 1
            assert len(result["related_incidents"]) == 1
            assert len(result["incident_proofs"]) == 1
            emitted = [(call.args[1], call.args[2]) for call in mock_emit.await_args_list]
            resources = {
                payload["resource"]
                for event, payload in emitted
                if event == "AT_XDR_REQUEST"
            }
            assert resources == {
                "alert_proof",
                "endpoint_logs",
                "assets",
                "incidents",
                "incident_proof",
            }
            complete = next(
                payload for event, payload in emitted
                if event == "AT_COLLECT_EVIDENCE_COMPLETE"
            )
            assert complete["endpoint_log_ids"] == ["log-1"]
            assert complete["incident_ids"] == ["inc-1"]
            assert complete["endpoint_log_query_mode"] == "linked_ids"

    @pytest.mark.asyncio
    async def test_missing_log_ids_falls_back_to_asset_time_window(self):
        state = _base_state(alert={
            "uuid": "alert-123",
            "assetId": "asset-1",
            "hostIp": "10.20.30.40",
            "occurTimestamp": 1_786_098_100_000,
        })
        with patch("clients.xdr_client.get_alert_proof", new_callable=AsyncMock) as mock_proof, \
             patch("clients.xdr_client.list_endpoint_security_logs", new_callable=AsyncMock) as mock_logs, \
             patch("clients.xdr_client.get_assets", new_callable=AsyncMock) as mock_assets, \
             patch("clients.xdr_client.list_incidents", new_callable=AsyncMock) as mock_incidents, \
             patch("alert_triage.graph_core._emit", new_callable=AsyncMock) as mock_emit:
            mock_proof.return_value = {"description": "proof without log ids"}
            mock_logs.return_value = [{"uuId": "log-window-1"}]
            mock_assets.return_value = [{"assetId": "asset-1"}]
            mock_incidents.return_value = []

            result = await collect_evidence(state)

        mock_logs.assert_awaited_once_with({
            "page": 1,
            "pageSize": 100,
            "startTimestamp": 1_786_097_500,
            "endTimestamp": 1_786_098_700,
            "assetIds": ["asset-1"],
            "hostIps": ["10.20.30.40"],
        })
        assert result["endpoint_logs"] == [{"uuId": "log-window-1"}]
        assert "endpoint_logs" not in result["missing_evidence"]
        complete = next(
            call.args[2] for call in mock_emit.await_args_list
            if call.args[1] == "AT_COLLECT_EVIDENCE_COMPLETE"
        )
        assert complete["endpoint_log_query_mode"] == "asset_time_window"

    @pytest.mark.asyncio
    async def test_persist_result_references_assets_and_incident_proofs(self):
        state = _base_state(
            alert={"uuid": "alert-123", "name": "WebShell"},
            alert_proof={"filePath": "C:\\\\inetpub\\\\wwwroot\\\\cmd.jsp"},
            endpoint_logs=[{"uuId": "log-1", "processName": "cmd.exe"}],
            assets=[{"assetId": "asset-1", "hostName": "app-prod-01"}],
            related_incidents=[{"uuId": "inc-1", "name": "WebShell incident"}],
            incident_proofs=[{"uuId": "inc-1", "proof": {"attackStory": ["upload"]}}],
            raw_decision={
                "verdict": "true_positive",
                "confidence": 0.9,
                "severity": "critical",
                "summary": "confirmed",
                "reasoning": "evidence",
                "recommended_actions": [],
                "missing_evidence": ["file_hash"],
            },
        )
        result = await persist_result(state)
        refs = result["result"]["xdr_evidence_refs"]
        assert {ref["source"] for ref in refs} >= {"asset", "incident_proof"}
        assert result["result"]["missing_evidence"] == []
        assert result["result"]["enrichment_evidence"] == ["file_hash"]

    @pytest.mark.asyncio
    async def test_alert_name_is_not_used_as_asset_hostname(self):
        state = _base_state(alert={"uuid": "alert-123", "name": "Suspicious script"})
        with patch("clients.xdr_client.get_alert_proof", new_callable=AsyncMock) as mock_proof, \
             patch("clients.xdr_client.get_assets", new_callable=AsyncMock) as mock_assets, \
             patch("clients.xdr_client.list_incidents", new_callable=AsyncMock) as mock_incidents:
            mock_proof.return_value = {"description": "summary only"}
            mock_incidents.return_value = []
            result = await collect_evidence(state)
            mock_assets.assert_not_awaited()
            assert "assets" in result["missing_evidence"]

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
            assert "assets" in result["missing_evidence"]


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
        result = await query_rag(state)
        assert result["rag_degraded"] is True
        assert "RAG_MCP_DISABLED" in result["warnings"]

    @pytest.mark.asyncio
    async def test_rag_success_keeps_mcp_citations(self):
        state = _base_state(alert={"alert_type": "malware"})
        response = type("Response", (), {
            "degraded": False,
            "answer": "[RAG:krf1.test] threat context",
            "citations": [{"chunk_id": "krf1.test", "source": "ATT&CK"}],
        })()
        with patch("clients.rag_client.query_rag", new_callable=AsyncMock, return_value=response):
            result = await query_rag(state)
        assert result["rag_degraded"] is False
        assert result["rag_response"]["citations"][0]["chunk_id"] == "krf1.test"


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
    async def test_complete_primary_evidence_uses_suspicious_instead_of_insufficient(self):
        state = _base_state(
            raw_decision={"verdict": "insufficient_evidence", "confidence": 0.3},
            missing_evidence=[],
        )
        result = await validate_decision(state)
        assert result["raw_decision"]["verdict"] == "suspicious"
        assert result["raw_decision"]["confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_deterministic_verdict_with_missing_evidence(self):
        state = _base_state(
            raw_decision={"verdict": "true_positive", "confidence": 0.7, "severity": "high"},
            missing_evidence=["alert_proof"],
        )
        result = await validate_decision(state)
        assert result["raw_decision"]["verdict"] == "suspicious"

    @pytest.mark.asyncio
    async def test_false_positive_requires_exact_whitelist(self):
        state = _base_state(
            raw_decision={"verdict": "false_positive", "confidence": 0.9, "severity": "medium"},
            whitelist_matches=[],
        )
        result = await validate_decision(state)
        assert result["raw_decision"]["verdict"] == "suspicious"
        assert result["raw_decision"]["confidence"] == 0.7
        assert any("exact whitelist" in error for error in result["validation_errors"])

    @pytest.mark.asyncio
    async def test_complete_suspicious_evidence_uses_stable_confidence_band(self):
        low = await validate_decision(_base_state(
            raw_decision={"verdict": "suspicious", "confidence": 0.4},
            missing_evidence=[],
        ))
        high = await validate_decision(_base_state(
            raw_decision={"verdict": "suspicious", "confidence": 0.95},
            missing_evidence=[],
        ))

        assert low["raw_decision"]["confidence"] == 0.45
        assert high["raw_decision"]["confidence"] == 0.8
        assert any("0.40->0.45" in warning for warning in low["warnings"])
        assert any("0.95->0.80" in warning for warning in high["warnings"])

    @pytest.mark.asyncio
    async def test_confidence_floor_does_not_raise_invalid_or_incomplete_results(self):
        invalid = await validate_decision(_base_state(
            raw_decision={"verdict": "suspicious", "confidence": "high"},
            missing_evidence=[],
        ))
        incomplete = await validate_decision(_base_state(
            raw_decision={"verdict": "suspicious", "confidence": 0.3},
            missing_evidence=["endpoint_logs"],
        ))

        assert invalid["raw_decision"]["confidence"] == 0.3
        assert incomplete["raw_decision"]["confidence"] == 0.3

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
