"""XDR API Client -- Encapsulates interaction with trustguard-xdr-mock.

Uses the official Sangfor XDR OpenAPI SDK signing format.
Only wraps business interfaces, explicitly excludes /mock/v1/** and ground-truth.
"""
from __future__ import annotations

import binascii
import hashlib
import hmac
import json as _json
import logging
import os
import struct
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────
XDR_BASE_URL = os.getenv("XDR_BASE_URL", "http://localhost:8443").rstrip("/")
XDR_AK = os.getenv("XDR_AK", "").strip()
XDR_SK = os.getenv("XDR_SK", "").strip()
XDR_TIMEOUT = float(os.getenv("XDR_TIMEOUT", "30.0") or "30.0")
XDR_MAX_RETRIES = int(os.getenv("XDR_MAX_RETRIES", "2") or "2")

# ── SDK signing constants ─────────────────────────────────────────
_SDK_HOST_KEY = "sdk-host"
_SDK_CONTENT_TYPE_KEY = "sdk-content-type"
_SIGN_DATE_KEY = "sign-date"
_AUTH_HEADER_KEY = "Authorization"
_DEFAULT_CONTENT_TYPE = "application/json"
EMPTY_PAYLOAD_HASH = "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855"


class XDRClientError(Exception):
    """Unified XDR Client error."""

    def __init__(
        self, message: str, status_code: Optional[int] = None, body: Any = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ── SDK signing primitives (replicating signing/canonical.py) ──────


def _sha256_hex_upper(data: bytes) -> str:
    return binascii.hexlify(hashlib.sha256(data).digest()).decode("utf-8").upper()


def _hmac_sha256_hex_upper(secret_key: str, data: str) -> str:
    mac = hmac.new(
        secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    )
    return binascii.hexlify(mac.digest()).decode("utf-8").upper()


def _url_transform(path: str) -> str:
    """URL path encoding + trailing '/' as required by SDK."""
    if not path.endswith("/"):
        path += "/"
    return urllib.parse.quote(path, encoding="utf-8")


def _payload_transform(payload: bytes | str | None) -> str:
    """Body transform: signed-byte-sort → strip spaces → SHA256 → upper hex."""
    if payload is None:
        payload = b""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    payload = bytes(payload)
    if not payload:
        return EMPTY_PAYLOAD_HASH
    # Signed int8 unpack then sort (negatives come before positives)
    byte_values = [struct.unpack("b", bytes([b]))[0] for b in payload]
    byte_values.sort()
    new_payload = bytearray()
    for bv in byte_values:
        new_payload.append(bv & 0xFF)
    # Remove all 0x20 (space) bytes
    filtered = bytes(b for b in new_payload if b != 0x20)
    return _sha256_hex_upper(filtered)


def _sdk_sign(
    method: str,
    path: str,
    body: bytes | str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Generate request headers with Authorization signature per Sangfor SDK."""
    if not XDR_AK or not XDR_SK:
        return {"Content-Type": "application/json"}

    host = urllib.parse.urlparse(XDR_BASE_URL).netloc
    sign_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Inject signature headers
    headers: dict[str, str] = {
        _SDK_HOST_KEY: host,
        _SDK_CONTENT_TYPE_KEY: _DEFAULT_CONTENT_TYPE,
        _SIGN_DATE_KEY: sign_date,
    }

    # Sort by lowercased key, build header block + signed-headers string
    sorted_keys = sorted(headers.keys(), key=lambda k: k.lower())
    header_lines = []
    signed_parts = []
    for k in sorted_keys:
        header_lines.append(f"{k}:{headers[k]}\n")
        signed_parts.append(f"{k};")
    header_block = "".join(header_lines)
    signed_headers = "".join(signed_parts)[:-1]  # strip trailing ;

    # Query string
    query_str = ""
    if params:
        sorted_params = sorted(params.items(), key=lambda x: x[0])
        query_str = urllib.parse.urlencode(sorted_params).replace("%3D", "=")

    # Canonical request
    canonical = (
        method.upper()
        + "\n"
        + _url_transform(path)
        + "\n"
        + query_str
        + "\n"
        + header_block
        + signed_headers
        + "\n"
        + _payload_transform(body)
    )
    hashed_canonical = _sha256_hex_upper(canonical.encode("utf-8"))
    total_str = "HMAC-SHA256\n" + sign_date + "\n" + hashed_canonical
    signature = _hmac_sha256_hex_upper(XDR_SK, total_str)

    auth_value = (
        f"algorithm=HMAC-SHA256, Access={XDR_AK}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers[_AUTH_HEADER_KEY] = auth_value
    headers["Content-Type"] = "application/json"
    return headers


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact sensitive auth info from log output."""
    redacted = dict(headers)
    for key in (_AUTH_HEADER_KEY,):
        if key in redacted:
            redacted[key] = "***REDACTED***"
    return redacted


# ── Core HTTP request ─────────────────────────────────────────────


async def _xdr_request(
    method: str,
    path: str,
    *,
    json_body: Any = None,
    params: Optional[dict[str, Any]] = None,
) -> Any:
    """Send XDR API request with retry and unified error handling."""
    body_bytes: bytes | None = None
    if json_body is not None:
        body_bytes = _json.dumps(
            json_body, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")

    headers = _sdk_sign(method, path, body=body_bytes, params=params)
    url = f"{XDR_BASE_URL}{path}"

    last_error: Optional[Exception] = None
    for attempt in range(XDR_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=XDR_TIMEOUT) as client:
                logger.debug(
                    "XDR request attempt=%d method=%s path=%s headers=%s",
                    attempt + 1,
                    method,
                    path,
                    _redact_headers(headers),
                )
                resp = await client.request(
                    method=method,
                    url=url,
                    content=body_bytes,
                    params=params,
                    headers=headers,
                )
                if resp.status_code >= 500:
                    raise XDRClientError(
                        f"XDR server error: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                        body=resp.text[:500],
                    )
                if resp.status_code == 404:
                    raise XDRClientError(
                        f"XDR resource not found: {path}", status_code=404
                    )
                if resp.status_code >= 400:
                    raise XDRClientError(
                        f"XDR client error: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                        body=resp.text[:500],
                    )
                if resp.status_code == 204 or not resp.content:
                    return None
                return resp.json()
        except XDRClientError as e:
            if attempt < XDR_MAX_RETRIES and e.status_code and e.status_code >= 500:
                logger.warning(
                    "XDR retry attempt=%d after error: %s", attempt + 1, e
                )
                await _async_sleep(1.0 * (attempt + 1))
                last_error = e
                continue
            raise
        except httpx.TimeoutException:
            logger.warning("XDR timeout attempt=%d path=%s", attempt + 1, path)
            if attempt < XDR_MAX_RETRIES:
                await _async_sleep(1.0 * (attempt + 1))
                last_error = XDRClientError(f"XDR timeout: {path}")
                continue
            raise XDRClientError(
                f"XDR timeout after {XDR_MAX_RETRIES + 1} attempts: {path}"
            )
        except httpx.HTTPError as e:
            logger.warning(
                "XDR transport error attempt=%d: %s", attempt + 1, e
            )
            if attempt < XDR_MAX_RETRIES:
                await _async_sleep(1.0 * (attempt + 1))
                last_error = e
                continue
            raise XDRClientError(f"XDR transport error: {e}") from e

    raise last_error or XDRClientError("XDR request failed")


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


# ── Business interfaces ────────────────────────────────────────────


async def get_alert(alert_uuid: str) -> dict[str, Any]:
    """Fetch single alert via list endpoint (mock has no dedicated GET /alerts/{uuid})."""
    alerts = await list_alerts({"uuIds": [alert_uuid], "page": 1, "page_size": 1})
    if alerts and isinstance(alerts[0], dict):
        return alerts[0]
    # fallback: try direct GET for production XDR compatibility
    try:
        result = await _xdr_request("GET", f"/api/xdr/v1/alerts/{alert_uuid}")
        return result if isinstance(result, dict) else {}
    except XDRClientError:
        return {}


async def list_alerts(
    params: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """POST /api/xdr/v1/alerts/list"""
    result = await _xdr_request(
        "POST", "/api/xdr/v1/alerts/list", json_body=params or {}
    )
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        data = result.get("data", {})
        if isinstance(data, dict):
            return data.get("item") or data.get("items") or data.get("alerts") or []
        return data if isinstance(data, list) else []
    return []


async def get_alert_proof(alert_uuid: str) -> dict[str, Any]:
    """GET /api/xdr/v1/alerts/{uuid}/proof"""
    path = f"/api/xdr/v1/alerts/{alert_uuid}/proof"
    result = await _xdr_request("GET", path)
    return result if isinstance(result, dict) else {}


async def list_incidents(
    params: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """POST /api/xdr/v1/incidents/list"""
    result = await _xdr_request(
        "POST", "/api/xdr/v1/incidents/list", json_body=params or {}
    )
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        data = result.get("data", {})
        if isinstance(data, dict):
            return data.get("item") or data.get("items") or data.get("incidents") or []
        return data if isinstance(data, list) else []
    return []


async def get_incident_proof(incident_uuid: str) -> dict[str, Any]:
    """GET /api/xdr/v1/incidents/{uuid}/proof"""
    path = f"/api/xdr/v1/incidents/{incident_uuid}/proof"
    result = await _xdr_request("GET", path)
    return result if isinstance(result, dict) else {}


async def get_assets(
    params: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """POST /api/xdr/v1/assets/list"""
    result = await _xdr_request(
        "POST", "/api/xdr/v1/assets/list", json_body=params or {}
    )
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        data = result.get("data", {})
        if isinstance(data, dict):
            return data.get("item") or data.get("items") or data.get("assets") or []
        return data if isinstance(data, list) else []
    return []


async def match_whitelist(
    params: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """POST /api/xdr/v1/whitelists/list"""
    result = await _xdr_request(
        "POST", "/api/xdr/v1/whitelists/list", json_body=params or {}
    )
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        data = result.get("data", {})
        if isinstance(data, dict):
            return data.get("item") or data.get("items") or data.get("matches") or []
        return data if isinstance(data, list) else []
    return []


async def get_disposition_status(
    entity_type: str, entity_uuid: str
) -> dict[str, Any]:
    """GET /api/xdr/v1/disposition/{entity_type}/{uuid}"""
    path = f"/api/xdr/v1/disposition/{entity_type}/{entity_uuid}"
    result = await _xdr_request("GET", path)
    return result if isinstance(result, dict) else {}


async def health_check() -> bool:
    """Check if XDR Mock is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{XDR_BASE_URL}/health")
            return resp.status_code < 500
    except Exception:
        return False
