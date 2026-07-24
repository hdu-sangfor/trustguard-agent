from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException


class RagClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (
            base_url
            or os.getenv("RAG_SERVICE_BASE_URL", "http://localhost:18200")
        ).rstrip("/")

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float = 20.0,
    ) -> Any:
        """Call a fixed RAG path and translate upstream errors at the BFF boundary."""
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json_body,
                    params=params,
                    data=data,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=504,
                detail="RAG 服务请求超时",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail="RAG 服务不可用",
            ) from exc

        if response.status_code >= 400:
            detail = f"RAG 服务返回 HTTP {response.status_code}"
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    candidate = payload.get("message") or payload.get("detail")
                    if isinstance(candidate, str) and candidate.strip():
                        detail = candidate.strip()
            except ValueError:
                pass
            mapped_status = (
                response.status_code
                if response.status_code < 500
                else 503
            )
            raise HTTPException(status_code=mapped_status, detail=detail)

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="RAG 服务返回了无效 JSON",
            ) from exc


rag_client = RagClient()
