import json

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients import llm_client


@pytest.mark.asyncio
async def test_openai_decision_requests_disable_thinking_on_format_retry(monkeypatch):
    payloads = []

    async def fake_stream(_cfg, _headers, payload):
        payloads.append(payload)
        if len(payloads) == 1:
            return ["not-json"], None
        return ['{"ok": true}'], None

    async def fake_emit_trace(*_args, **_kwargs):
        return None

    def parse_content(content):
        try:
            return json.loads(content), ""
        except json.JSONDecodeError as exc:
            return None, str(exc)

    monkeypatch.setattr(llm_client, "_stream_chat_completions_collect", fake_stream)
    monkeypatch.setattr(llm_client, "_emit_llm_trace", fake_emit_trace)

    cfg = llm_client.LLMProviderConfig(
        provider=llm_client.LLMProvider.OPENAI_COMPAT,
        provider_source="openai_compat",
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model_id="deepseek-v4-flash",
        connect_timeout=1,
        read_timeout=1,
        max_retries=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        format_retries=1,
        json_mode=True,
    )

    parsed, usage = await llm_client._run_decision_llm_parse_loop(
        "task-test",
        cfg,
        "system",
        "user",
        parse_content,
    )

    assert parsed == {"ok": True}
    assert usage is None
    assert [payload["thinking"] for payload in payloads] == [
        {"type": "disabled"},
        {"type": "disabled"},
    ]


@pytest.mark.asyncio
async def test_non_deepseek_decision_request_does_not_send_thinking(monkeypatch):
    payloads = []

    async def fake_stream(_cfg, _headers, payload):
        payloads.append(payload)
        return ['{"ok": true}'], None

    async def fake_emit_trace(*_args, **_kwargs):
        return None

    monkeypatch.setattr(llm_client, "_stream_chat_completions_collect", fake_stream)
    monkeypatch.setattr(llm_client, "_emit_llm_trace", fake_emit_trace)

    cfg = llm_client.LLMProviderConfig(
        provider=llm_client.LLMProvider.OPENAI_COMPAT,
        provider_source="openai_compat",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model_id="gpt-test",
        connect_timeout=1,
        read_timeout=1,
        max_retries=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        format_retries=0,
        json_mode=True,
    )

    parsed, _usage = await llm_client._run_decision_llm_parse_loop(
        "task-test",
        cfg,
        "system",
        "user",
        lambda content: (json.loads(content), ""),
    )

    assert parsed == {"ok": True}
    assert "thinking" not in payloads[0]
