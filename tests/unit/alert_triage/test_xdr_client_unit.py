"""XDR Client 单元测试。

覆盖：签名生成、错误处理、脱敏。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "orchestrator" / "app"))

import os
from unittest.mock import AsyncMock, patch

import pytest

from clients.xdr_client import (
    XDRClientError,
    _sdk_sign,
    _redact_headers,
    get_alert,
    get_alert_proof,
    list_endpoint_security_logs,
    match_whitelist,
    list_incidents,
    get_assets,
    health_check,
)


class TestXDRSignature:
    def test_signature_with_secret(self):
        with patch.dict(os.environ, {"XDR_AK": "test-ak", "XDR_SK": "test-secret"}):
            # Need to re-import to pick up env
            import importlib
            import clients.xdr_client as xdrmod
            importlib.reload(xdrmod)
            headers = xdrmod._sdk_sign("GET", "/api/test")
            assert headers["Authorization"].startswith("algorithm=HMAC-SHA256")
            assert "Signature=" in headers["Authorization"]

    def test_signature_without_secret(self):
        with patch.dict(os.environ, {"XDR_AK": "", "XDR_SK": ""}):
            import importlib
            import clients.xdr_client as xdrmod
            importlib.reload(xdrmod)
            headers = xdrmod._sdk_sign("GET", "/api/test")
            assert "Authorization" not in headers


class TestHeaders:
    def test_headers_include_signature_and_api_key(self):
        with patch.dict(os.environ, {"XDR_AK": "key123", "XDR_SK": "sec"}):
            import importlib
            import clients.xdr_client as xdrmod
            importlib.reload(xdrmod)
            headers = xdrmod._sdk_sign("POST", "/api/test", '{"key":"value"}')
            assert "sign-date" in headers
            assert "sdk-host" in headers
            assert "Authorization" in headers
            assert "Access=key123" in headers["Authorization"]


class TestRedaction:
    def test_redact_headers(self):
        headers = {
            "Authorization": "algorithm=HMAC-SHA256, Signature=abc123def456",
            "Content-Type": "application/json",
        }
        redacted = _redact_headers(headers)
        assert redacted["Authorization"] == "***REDACTED***"
        assert redacted["Content-Type"] == "application/json"


class TestErrorHandling:
    def test_xdr_client_error(self):
        err = XDRClientError("test error", status_code=500)
        assert str(err) == "test error"
        assert err.status_code == 500
        assert err.body is None

    def test_xdr_client_error_with_body(self):
        err = XDRClientError("test", status_code=404, body='{"msg":"not found"}')
        assert err.status_code == 404


class TestClientCalls:
    @pytest.mark.asyncio
    async def test_get_alert(self):
        mock_resp = {"uuid": "alert-1", "name": "test alert", "severity": "high"}
        with patch("clients.xdr_client._xdr_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp
            result = await get_alert("alert-1")
            assert result == mock_resp

    @pytest.mark.asyncio
    async def test_get_alert_not_found(self):
        with patch("clients.xdr_client._xdr_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = XDRClientError("not found", status_code=404)
            with pytest.raises(XDRClientError):
                await get_alert("nonexistent")

    @pytest.mark.asyncio
    async def test_get_alert_proof_unwraps_official_response(self):
        with patch("clients.xdr_client._xdr_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"code": 0, "data": {"uuId": "alert-1", "proof": {"logIds": ["log-1"]}}}
            result = await get_alert_proof("alert-1")
            assert result == {"uuId": "alert-1", "proof": {"logIds": ["log-1"]}}

    @pytest.mark.asyncio
    async def test_endpoint_security_logs_unwrap_official_page(self):
        with patch("clients.xdr_client._xdr_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"code": 0, "data": {"item": [{"uuId": "log-1"}]}}
            result = await list_endpoint_security_logs({"uuIds": ["log-1"]})
            assert result == [{"uuId": "log-1"}]

    @pytest.mark.asyncio
    async def test_match_whitelist(self):
        mock_matches = [{"id": "wl-1", "rule_id": "r1", "name": "asset inventory"}]
        with patch("clients.xdr_client._xdr_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_matches
            result = await match_whitelist({"alert_uuid": "alert-1"})
            assert result == mock_matches

    @pytest.mark.asyncio
    async def test_health_check_ok(self):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.is_success = True
            result = await health_check()
            assert result is True
