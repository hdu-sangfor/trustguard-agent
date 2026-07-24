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
    _generate_xdr_signature,
    _build_xdr_headers,
    _redact_headers,
    get_alert,
    get_alert_proof,
    match_whitelist,
    list_incidents,
    get_assets,
    health_check,
)


class TestXDRSignature:
    def test_signature_with_secret(self):
        with patch.dict(os.environ, {"XDR_SIGN_SECRET": "test-secret"}):
            # Need to re-import to pick up env
            import importlib
            import clients.xdr_client as xdrmod
            importlib.reload(xdrmod)
            sig = xdrmod._generate_xdr_signature("GET", "/api/test", "12345", "abc", "")
            assert isinstance(sig, str)
            assert len(sig) == 64  # HMAC-SHA256 hex

    def test_signature_without_secret(self):
        with patch.dict(os.environ, {"XDR_SIGN_SECRET": ""}):
            import importlib
            import clients.xdr_client as xdrmod
            importlib.reload(xdrmod)
            sig = xdrmod._generate_xdr_signature("GET", "/api/test", "12345", "abc", "")
            assert sig == ""


class TestHeaders:
    def test_headers_include_signature_and_api_key(self):
        with patch.dict(os.environ, {"XDR_SIGN_SECRET": "sec", "XDR_API_KEY": "key123"}):
            import importlib
            import clients.xdr_client as xdrmod
            importlib.reload(xdrmod)
            headers = xdrmod._build_xdr_headers("POST", "/api/test", '{"key":"value"}')
            assert "X-Timestamp" in headers
            assert "X-Nonce" in headers
            assert "X-Signature" in headers
            assert "X-API-Key" in headers
            assert headers["X-API-Key"] == "key123"


class TestRedaction:
    def test_redact_headers(self):
        headers = {
            "X-Signature": "abc123def456",
            "X-API-Key": "sk-secret-key",
            "Content-Type": "application/json",
        }
        redacted = _redact_headers(headers)
        assert redacted["X-Signature"] == "***REDACTED***"
        assert redacted["X-API-Key"] == "***REDACTED***"
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
