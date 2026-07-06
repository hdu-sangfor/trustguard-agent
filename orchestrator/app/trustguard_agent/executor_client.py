from __future__ import annotations

import os
from typing import Any

def _local_demo_tool(tool_id: str, target: str, allowed_target: str | None) -> dict[str, Any]:
    from urllib.parse import urlparse

    def host(value: str) -> str:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        return parsed.hostname or value

    if allowed_target:
        target_host = host(target)
        allowed_host = host(allowed_target)
        if target_host != allowed_host and not target_host.endswith(f".{allowed_host}"):
            return {
                "status": "FAILED",
                "tool_id": tool_id,
                "target": target,
                "summary": f"target {target!r} is outside allowed scope",
                "artifacts": {"code": "target_scope_denied"},
                "findings": [],
            }

    if tool_id == "http_probe":
        return {
            "status": "OK",
            "tool_id": tool_id,
            "target": target,
            "summary": "HTTP surface is reachable and exposes standard security headers.",
            "artifacts": {
                "status_code": 200,
                "title": "TrustGuard Demo App",
                "headers": {
                    "server": "nginx",
                    "x-frame-options": "SAMEORIGIN",
                    "content-security-policy": "default-src 'self'",
                },
            },
            "findings": [],
        }
    if tool_id == "fingerprint":
        return {
            "status": "OK",
            "tool_id": tool_id,
            "target": target,
            "summary": "Fingerprint indicates nginx reverse proxy with a Python API backend.",
            "artifacts": {
                "technologies": ["nginx", "python-fastapi", "swagger-ui"],
                "interesting_paths": ["/docs", "/openapi.json", "/health"],
            },
            "findings": [
                {
                    "id": "info-openapi-docs",
                    "severity": "info",
                    "title": "OpenAPI documentation is reachable",
                    "evidence": "/docs and /openapi.json were identified in the demo fingerprint.",
                }
            ],
        }
    if tool_id == "risk_review":
        return {
            "status": "OK",
            "tool_id": tool_id,
            "target": target,
            "summary": "Risk review found one medium-risk exposure and one informational finding.",
            "artifacts": {"checks": ["security_headers", "docs_exposure"], "risk_score": 61},
            "findings": [
                {
                    "id": "med-api-docs-exposed",
                    "severity": "medium",
                    "title": "API documentation exposed on target surface",
                    "evidence": "Swagger/OpenAPI endpoints can disclose route names, schemas, and auth patterns.",
                    "recommendation": "Restrict documentation endpoints in production or require authentication.",
                }
            ],
        }
    return {
        "status": "FAILED",
        "tool_id": tool_id,
        "target": target,
        "summary": f"unknown demo tool: {tool_id}",
        "artifacts": {"code": "unknown_tool"},
        "findings": [],
    }


def execute_tool(
    *,
    task_id: str,
    tool_id: str,
    target: str,
    allowed_target: str | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (os.getenv("TRUSTGUARD_EXECUTOR_MODE") or "http").strip().lower() == "inline":
        return _local_demo_tool(tool_id, target, allowed_target)

    base = (os.getenv("EXECUTOR_BASE_URL") or "http://localhost:18102").rstrip("/")
    payload = {
        "task_id": task_id,
        "skill_id": tool_id,
        "target": target,
        "allowed_target": allowed_target,
        "params": params or {},
        "execution_kind": "skill",
    }
    import httpx

    with httpx.Client(timeout=30.0) as client:
        response = client.post(f"{base}/v1/execute", json=payload)
        response.raise_for_status()
        data = dict(response.json())
        parsed = data.get("parsed_artifacts")
        if isinstance(parsed, dict):
            return {
                "status": data.get("status") or parsed.get("status") or "OK",
                "tool_id": tool_id,
                "target": target,
                "summary": parsed.get("summary") or data.get("raw_stdout") or "",
                "artifacts": parsed,
                "findings": parsed.get("findings") or [],
            }
        return {
            "status": data.get("status") or "OK",
            "tool_id": tool_id,
            "target": target,
            "summary": data.get("raw_stdout") or "",
            "artifacts": data,
            "findings": [],
        }
