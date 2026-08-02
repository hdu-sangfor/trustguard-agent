from __future__ import annotations

import pytest

from app.clients import llm_client


@pytest.mark.asyncio
async def test_native_llm_stream_emits_content_and_ignores_reasoning(monkeypatch):
    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"reasoning_content":"private"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"草稿"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"已生成"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return FakeStream()

    monkeypatch.setattr(llm_client, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.setenv("LLM_API_KEY", "unit-key")

    chunks = [chunk async for chunk in llm_client.stream_assistant_response({"status": "READY"}, "fallback")]

    assert "".join(chunks) == "草稿已生成"
    assert "private" not in "".join(chunks)
