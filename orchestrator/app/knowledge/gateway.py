from __future__ import annotations

import asyncio

from app.knowledge.config import KnowledgeMcpSettings
from app.knowledge.mcp_client import (
    KnowledgeCallContext,
    KnowledgeTransport,
    McpKnowledgeTransport,
    McpKnowledgeTransportError,
)
from app.knowledge.models import (
    KnowledgeResource,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)


class KnowledgeGateway:
    def __init__(
        self,
        *,
        transport: KnowledgeTransport,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def search(
        self,
        request: KnowledgeSearchRequest,
        *,
        context: KnowledgeCallContext,
    ) -> KnowledgeSearchResponse:
        last_error: McpKnowledgeTransportError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await self._transport.search(request, context=context)
            except McpKnowledgeTransportError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                await asyncio.sleep(min(0.25 * (2**attempt), 1.0))
            except TimeoutError as exc:
                last_error = McpKnowledgeTransportError(
                    "MCP_TIMEOUT",
                    "RAG MCP knowledge_search timed out",
                    retryable=True,
                )
                if attempt >= self._max_retries:
                    raise last_error from exc
        assert last_error is not None
        raise last_error

    async def read_resource(
        self,
        resource_uri: str,
        *,
        context: KnowledgeCallContext,
    ) -> KnowledgeResource:
        last_error: McpKnowledgeTransportError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await self._transport.read_resource(
                        resource_uri,
                        context=context,
                    )
            except McpKnowledgeTransportError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                await asyncio.sleep(min(0.25 * (2**attempt), 1.0))
            except TimeoutError as exc:
                last_error = McpKnowledgeTransportError(
                    "MCP_TIMEOUT",
                    "RAG MCP knowledge resource read timed out",
                    retryable=True,
                )
                if attempt >= self._max_retries:
                    raise last_error from exc
        assert last_error is not None
        raise last_error

    async def aclose(self) -> None:
        await self._transport.aclose()


_gateway: KnowledgeGateway | None = None


def get_knowledge_gateway(
    settings: KnowledgeMcpSettings | None = None,
) -> KnowledgeGateway:
    global _gateway
    if _gateway is None:
        active = settings or KnowledgeMcpSettings.from_env()
        _gateway = KnowledgeGateway(
            transport=McpKnowledgeTransport(active),
            timeout_seconds=active.timeout_seconds,
            max_retries=active.max_retries,
        )
    return _gateway


async def close_knowledge_gateway() -> None:
    global _gateway
    if _gateway is not None:
        await _gateway.aclose()
        _gateway = None
