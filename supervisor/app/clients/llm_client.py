from __future__ import annotations

import json
import os
from typing import Any

import httpx


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
        "max_duration_seconds,authorization_confirmed. Never invent a target or authorization. "
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
