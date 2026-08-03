from __future__ import annotations

import json
import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger("trustguard.supervisor.llm")


def llm_enabled() -> bool:
    key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    return bool(key and key not in {"sk-your-key", "replace-me"})


def extract_pentest_draft(message: str) -> dict[str, Any] | None:
    """Best-effort structured extraction; callers always apply deterministic validation."""
    if not llm_enabled():
        return None
    key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL_ID") or "gpt-4o-mini"
    system = (
        "Extract an authorized penetration-test task draft from the user message. "
        "Return JSON only with keys name,target,description,business_background,"
        "extra_user_requirements,test_profile,allow_exploit,allow_destructive_actions,"
        "max_duration_seconds. Never invent a target. "
        "Defaults: safe, exploit false, destructive false, duration 900."
    )
    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": message[:8000]},
                    ],
                },
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


async def stream_assistant_response(
    draft_result: dict[str, Any],
    fallback: str,
) -> AsyncIterator[str]:
    """Stream user-facing prose only; provider reasoning fields are ignored."""
    if not llm_enabled():
        for index in range(0, len(fallback), 6):
            yield fallback[index:index + 6]
            await asyncio.sleep(0.02)
        return

    key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL_ID") or "gpt-4o-mini"
    system = (
        "You are TrustGuard Supervisor. Write a concise Chinese response to the user based only on the "
        "validated workflow draft result. Explain missing fields or ask for confirmation. Do not reveal "
        "private chain-of-thought and do not add executable instructions."
    )
    safe_result = {
        key_name: value
        for key_name, value in draft_result.items()
        if key_name not in {"confirmationToken", "confirmation_token"}
    }
    emitted = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=None)) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(safe_result, ensure_ascii=False)},
                    ],
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        data = json.loads(payload)
                        # Deliberately ignore reasoning_content / reasoning fields.
                        content = data["choices"][0]["delta"].get("content") or ""
                    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                        continue
                    if content:
                        emitted = True
                        yield str(content)
    except Exception:
        if emitted:
            return

    if not emitted:
        for index in range(0, len(fallback), 6):
            yield fallback[index:index + 6]
            await asyncio.sleep(0.02)


async def generate_progress_summary(context: dict[str, Any], fallback: str) -> str:
    """Generate a user-facing audit summary; never expose provider reasoning fields."""
    if not llm_enabled():
        return fallback
    key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL_ID") or "gpt-4o-mini"
    max_tokens = max(256, min(int(os.getenv("SUPERVISOR_PROGRESS_MAX_TOKENS") or "1024"), 4096))
    system = (
        "你是 TrustGuard Supervisor。根据给定的真实渗透测试事件，用中文写一条简短阶段性进度回复。"
        "只描述已发生的阶段、工具结果、阻塞和下一步，不得声称尚未确证的漏洞，不得输出私有思维链，"
        "不得提供攻击载荷。控制在 120 字以内，直接面向用户。"
    )
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
        if not str(content).strip():
            log.warning("progress summary model returned empty content model=%s", model)
        return str(content).strip() or fallback
    except Exception:
        log.exception("progress summary generation failed model=%s", model)
        return fallback
