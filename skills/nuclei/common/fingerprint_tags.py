"""Derive stack_hint / default nuclei -tags from fingerprint rules (JSON)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_STACK_HINT_TAGS: dict[str, str] = {
    "java": "cve,rce,java,struts",
    "php": "cve,php,wordpress",
    "dotnet": "cve,asp,aspnet,iis",
    "nodejs": "cve,nodejs,express",
}


def load_fingerprint_rules(config_path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return []
    out: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        contains_any = rule.get("contains_any")
        regex_any = rule.get("regex_any")
        priority = int(rule.get("priority") or 0)
        stack_hint = str(rule.get("stack_hint") or "").strip().lower()
        if not stack_hint:
            continue
        keys = [str(x).strip().lower() for x in contains_any if str(x).strip()] if isinstance(contains_any, list) else []
        regexes: list[re.Pattern[str]] = []
        if isinstance(regex_any, list):
            for raw in regex_any:
                s = str(raw).strip()
                if not s:
                    continue
                try:
                    regexes.append(re.compile(s, re.I))
                except re.error:
                    continue
        if not keys and not regexes:
            continue
        nuclei_tags = str(rule.get("nuclei_tags") or "").strip()
        out.append(
            {
                "priority": priority,
                "contains_any": keys,
                "regex_any": regexes,
                "stack_hint": stack_hint,
                "nuclei_tags": nuclei_tags,
            }
        )
    out.sort(key=lambda x: int(x.get("priority") or 0), reverse=True)
    return out


def derive_nuclei_tags_override(fp_lower: str, rules: list[dict[str, Any]]) -> str:
    """
    若规则命中且配置了 nuclei_tags，则覆盖默认 stack_hint → tags 映射（用于缩小 Nuclei 模板域）。
    """
    if not fp_lower or not rules:
        return ""
    for rule in rules:
        keys = rule.get("contains_any") if isinstance(rule.get("contains_any"), list) else []
        regexes = rule.get("regex_any") if isinstance(rule.get("regex_any"), list) else []
        hit_kw = any(k in fp_lower for k in keys)
        hit_rx = any(rx.search(fp_lower) for rx in regexes if hasattr(rx, "search"))
        if hit_kw or hit_rx:
            nt = str(rule.get("nuclei_tags") or "").strip()
            if nt:
                return nt
    return ""


def derive_stack_hint(
    params: dict[str, Any],
    context: dict[str, Any],
    rules: list[dict[str, Any]],
) -> str:
    hint = str(params.get("stack_hint") or context.get("stack_hint") or "").strip().lower()
    if hint:
        return hint
    fp = str(context.get("fingerprint") or context.get("whatweb") or "").lower()
    if not fp:
        return ""
    for rule in rules:
        keys = rule.get("contains_any") if isinstance(rule.get("contains_any"), list) else []
        regexes = rule.get("regex_any") if isinstance(rule.get("regex_any"), list) else []
        hit_kw = any(k in fp for k in keys)
        hit_rx = any(rx.search(fp) for rx in regexes if hasattr(rx, "search"))
        if hit_kw or hit_rx:
            return str(rule.get("stack_hint") or "").lower()
    return ""


def default_tags_for_stack(stack_hint: str) -> str:
    return _STACK_HINT_TAGS.get(stack_hint, "")
