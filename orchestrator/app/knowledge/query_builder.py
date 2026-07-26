from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


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
    query = " ".join((query_text or "").split())[:2000]
    if not query:
        return None

    todo = target_context.get("_current_todo")
    todo_payload = todo if isinstance(todo, dict) else {}
    relevant = {
        "phase": phase,
        "todo_id": str(todo_payload.get("id") or "")[:128],
        "todo_name": str(todo_payload.get("name") or "")[:256],
        "fingerprints": _stable_value(target_context.get("fingerprints"), 1000),
        "whatweb": _stable_value(
            target_context.get("whatweb-fingerprint_fingerprints"),
            1000,
        ),
        "confirmed_cve": _stable_value(target_context.get("confirmed_cve"), 1000),
        "vuln_confirmed": bool(target_context.get("vuln_confirmed")),
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
