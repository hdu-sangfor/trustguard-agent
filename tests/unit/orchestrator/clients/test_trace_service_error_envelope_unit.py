"""编排器解析Evidence 4xx 错误体（EvidenceErrorBody）供日志 — 纯单测，无需真实 evidence 进程。"""
import json
import os

import httpx

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients.evidence_http_error import evidence_http_error_detail


def _resp(status: int, body: str) -> httpx.Response:
    return httpx.Response(
        status,
        text=body,
        request=httpx.Request("GET", "http://localhost:18103/health"),
    )


def test_validation_error_envelope_detail():
    body = json.dumps(
        {
            "code": "VALIDATION_ERROR",
            "message": "request validation failed",
            "trace_id": "deadbeef0001",
            "field_errors": {"skillId": "must not be blank"},
        }
    )
    s = evidence_http_error_detail(_resp(400, body))
    assert "status=400" in s
    assert "VALIDATION_ERROR" in s
    assert "deadbeef0001" in s
    assert "skillId" in s


def test_non_json_body_preview():
    s = evidence_http_error_detail(_resp(502, "upstream timeout"))
    assert "status=502" in s
    assert "upstream" in s


def test_plain_json_fallback_detail():
    body = json.dumps({"status": "error", "message": "workspace write failed"})
    s = evidence_http_error_detail(_resp(500, body))
    assert "status=500" in s
    assert "workspace" in s
