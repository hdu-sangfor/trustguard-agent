from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.kb_query_purify import redact_ips_and_urls

_MAX_QUERY_CHARS = 2000


@dataclass(frozen=True)
class PenetrationKnowledgeQuery:
    query: str
    fingerprint: str
    trigger: str


def build_penetration_knowledge_query(
    *,
    phase: str,
    query_text: str,
    target_context: dict[str, Any],
) -> PenetrationKnowledgeQuery | None:
    base_query = " ".join((query_text or "").split())
    security_hints = _security_query_hints(target_context)
    query_parts = [*security_hints, base_query]
    query = "\n".join(part for part in query_parts if part)[:_MAX_QUERY_CHARS]
    if not query.strip():
        return None

    todo = target_context.get("_current_todo")
    todo_payload = todo if isinstance(todo, dict) else {}
    relevant = {
        "phase": phase,
        "todo_id": str(todo_payload.get("id") or "")[:128],
        "todo_name": str(todo_payload.get("name") or "")[:256],
        "framework_target": _stable_value(
            target_context.get("framework_target"),
            256,
        ),
        "framework_hint": _stable_value(
            target_context.get("framework_hint"),
            256,
        ),
        "fingerprints": _stable_value(target_context.get("fingerprints"), 1000),
        "whatweb": _stable_value(
            target_context.get("whatweb-fingerprint_fingerprints"),
            1000,
        ),
        "confirmed_cve": _stable_value(target_context.get("confirmed_cve"), 1000),
        "vuln_confirmed": bool(
            target_context.get("vuln_confirmed")
            or target_context.get("vulnerability_confirmed")
        ),
        "exploit_ready": bool(target_context.get("exploit_ready")),
    }
    encoded = json.dumps(
        relevant,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return PenetrationKnowledgeQuery(
        query=query,
        fingerprint=hashlib.sha256(encoded).hexdigest(),
        trigger="phase_or_security_context_change",
    )


def _security_query_hints(target_context: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for label, value, max_chars in (
        ("framework_target", target_context.get("framework_target"), 128),
        ("framework_hint", target_context.get("framework_hint"), 128),
        ("fingerprints", target_context.get("fingerprints"), 400),
        (
            "whatweb_fingerprints",
            target_context.get("whatweb-fingerprint_fingerprints"),
            400,
        ),
        ("confirmed_cve", target_context.get("confirmed_cve"), 256),
    ):
        normalized = _query_hint_value(value, max_chars=max_chars)
        if normalized:
            hints.append(f"{label}={normalized}")

    if target_context.get("vuln_confirmed") or target_context.get(
        "vulnerability_confirmed"
    ):
        hints.append("vuln_confirmed=true")
    if target_context.get("exploit_ready"):
        hints.append("exploit_ready=true")
    return hints


def _query_hint_value(value: Any, *, max_chars: int) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        raw = value
    elif isinstance(value, (bool, int, float)):
        raw = str(value)
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            raw = str(value)
    normalized = " ".join(redact_ips_and_urls(raw).split())
    return normalized[:max_chars].strip()


def _stable_value(value: Any, max_chars: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, list):
        return [_stable_value(item, max_chars // 2) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _stable_value(item, max_chars // 2)
            for key, item in list(value.items())[:20]
        }
    return str(value)[:max_chars]
