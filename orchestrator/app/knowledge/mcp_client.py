from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.knowledge.config import KnowledgeMcpSettings
from app.knowledge.models import KnowledgeSearchRequest, KnowledgeSearchResponse


@dataclass(frozen=True)
class KnowledgeCallContext:
    task_id: str
    request_id: str
    workflow_type: str
    workspace_id: str


class KnowledgeTransport(Protocol):
    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        context: KnowledgeCallContext,
    ) -> KnowledgeSearchResponse: ...

    async def aclose(self) -> None: ...


class McpKnowledgeTransportError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class McpKnowledgeTransport:
    def __init__(self, settings: KnowledgeMcpSettings) -> None:
        self._settings = settings
        self._context_headers: ContextVar[dict[str, str] | None] = ContextVar(
            "knowledge_mcp_headers",
            default=None,
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": [self._inject_context_headers]},
        )
        self._contract_lock = asyncio.Lock()
        self._contract_validated = False

    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        context: KnowledgeCallContext,
    ) -> KnowledgeSearchResponse:
        token = self._context_headers.set(self._headers(context))
        try:
            async with (
                streamable_http_client(
                    self._settings.endpoint,
                    http_client=self._client,
                ) as (read_stream, write_stream, _),
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(
                        seconds=self._settings.timeout_seconds
                    ),
                ) as session,
            ):
                await session.initialize()
                await self._ensure_contract(session)
                result = await session.call_tool(
                    "knowledge_search",
                    request.model_dump(mode="json"),
                    read_timeout_seconds=timedelta(
                        seconds=self._settings.timeout_seconds
                    ),
                )
        except McpKnowledgeTransportError:
            raise
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise McpKnowledgeTransportError(
                "MCP_TIMEOUT",
                "RAG MCP knowledge_search timed out",
                retryable=True,
            ) from exc
        except Exception as exc:
            raise McpKnowledgeTransportError(
                "MCP_UNAVAILABLE",
                f"RAG MCP request failed: {str(exc)[:300]}",
                retryable=True,
            ) from exc
        finally:
            self._context_headers.reset(token)

        if result.isError:
            code, message, retryable = _tool_error(result.content)
            raise McpKnowledgeTransportError(code, message, retryable=retryable)
        if not isinstance(result.structuredContent, dict):
            raise McpKnowledgeTransportError(
                "MCP_SCHEMA_MISMATCH",
                "knowledge_search did not return structured content",
                retryable=False,
            )
        try:
            return KnowledgeSearchResponse.model_validate(result.structuredContent)
        except Exception as exc:
            raise McpKnowledgeTransportError(
                "MCP_SCHEMA_MISMATCH",
                f"knowledge_search response failed v1 validation: {str(exc)[:300]}",
                retryable=False,
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure_contract(self, session: ClientSession) -> None:
        if self._contract_validated:
            return
        async with self._contract_lock:
            if self._contract_validated:
                return
            tools = await session.list_tools()
            tool = next(
                (item for item in tools.tools if item.name == "knowledge_search"),
                None,
            )
            if tool is None:
                raise McpKnowledgeTransportError(
                    "MCP_CONTRACT_MISSING",
                    "RAG MCP does not expose knowledge_search",
                    retryable=False,
                )
            request_version = (
                tool.inputSchema.get("properties", {})
                .get("schema_version", {})
                .get("const")
            )
            response_version = (
                (tool.outputSchema or {})
                .get("properties", {})
                .get("schema_version", {})
                .get("const")
            )
            if request_version != "trustguard-knowledge-search-request-v1" or (
                response_version != "trustguard-knowledge-search-v1"
            ):
                raise McpKnowledgeTransportError(
                    "MCP_SCHEMA_MISMATCH",
                    "RAG MCP knowledge_search v1 schema is unavailable",
                    retryable=False,
                )
            self._contract_validated = True

    async def _inject_context_headers(self, request: httpx.Request) -> None:
        for name, value in (self._context_headers.get() or {}).items():
            request.headers[name] = value

    def _headers(self, context: KnowledgeCallContext) -> dict[str, str]:
        headers = {
            "X-Request-ID": context.request_id,
            "X-Task-ID": context.task_id,
            "X-Workflow-Type": context.workflow_type,
            "X-Workspace-ID": context.workspace_id,
        }
        if self._settings.access_token:
            headers["Authorization"] = f"Bearer {self._settings.access_token}"
        return headers


def _tool_error(content: list[Any]) -> tuple[str, str, bool]:
    text = ""
    for item in content:
        candidate = getattr(item, "text", None)
        if isinstance(candidate, str) and candidate.strip():
            text = candidate.strip()
            break
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return (
                str(payload.get("code") or "MCP_TOOL_ERROR"),
                str(payload.get("message") or "RAG MCP knowledge_search failed")[:500],
                bool(payload.get("retryable")),
            )
    except (TypeError, ValueError):
        pass
    return "MCP_TOOL_ERROR", (text or "RAG MCP knowledge_search failed")[:500], False
