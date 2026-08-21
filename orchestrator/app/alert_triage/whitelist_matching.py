"""Deterministic, conservative matching for XDR whitelist records.

The official whitelist list API returns candidate rules rather than a verdict
for the current alert.  This module applies the rule fields locally so an
active rule from another host or account cannot suppress an alert.
"""
from __future__ import annotations

import copy
import re
import time
from collections import defaultdict
from typing import Any


_ACTIVE_STATUSES = {"1", "true", "enabled", "enable", "active"}
_FIELD_ALIASES = {
    "commandline": ("commandline", "command_line", "processparam", "process_param"),
    "filepath": ("filepath", "file_path", "scriptpath", "script_path"),
    "username": ("username", "user_name", "user", "account"),
    "hostip": ("hostip", "host_ip", "ip", "sourceip", "source_ip"),
    "assetid": ("assetid", "asset_id", "hostassetid", "host_asset_id"),
    "processname": ("processname", "process_name"),
    "changeticket": ("changeticket", "change_ticket", "ticketid", "ticket_id"),
}


def _norm(value: Any) -> str:
    return str(value).strip().replace("/", "\\").casefold()


def _flatten(values: list[dict[str, Any]]) -> dict[str, list[Any]]:
    flattened: dict[str, list[Any]] = defaultdict(list)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                flattened[_norm_key(key)].append(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for value in values:
        visit(value)
    return flattened


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _context_values(flattened: dict[str, list[Any]], field: str) -> list[Any]:
    key = _norm_key(field)
    aliases = _FIELD_ALIASES.get(key, (key,))
    output: list[Any] = []
    for alias in aliases:
        output.extend(flattened.get(_norm_key(alias), []))
    return output


def _rule_matches(rule: dict[str, Any], flattened: dict[str, list[Any]]) -> bool:
    field = str(rule.get("field") or rule.get("key") or "").strip()
    if not field:
        return False
    expected = rule.get("value", rule.get("values"))
    operator = str(rule.get("operator") or rule.get("op") or "equals").casefold()
    actual_values = [_norm(item) for item in _context_values(flattened, field)]
    if not actual_values:
        return False
    if operator in {"in", "oneof"}:
        expected_values = expected if isinstance(expected, list) else [expected]
        return any(value in {_norm(item) for item in expected_values} for value in actual_values)
    expected_value = _norm(expected)
    if not expected_value:
        return False
    if operator in {"contains", "includes"}:
        return any(expected_value in value for value in actual_values)
    if operator in {"regex", "matches"}:
        try:
            return any(re.search(str(expected), value, re.IGNORECASE) is not None for value in actual_values)
        except re.error:
            return False
    return any(value == expected_value for value in actual_values)


def _timestamp(values: list[dict[str, Any]]) -> int:
    flattened = _flatten(values)
    for key in ("occurTimestamp", "occur_timestamp", "lastTimestamp", "uploadTimestamp"):
        for value in flattened.get(_norm_key(key), []):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return int(time.time())


def match_whitelist_records(
    records: list[dict[str, Any]],
    *,
    alert: dict[str, Any] | None = None,
    proof: dict[str, Any] | None = None,
    endpoint_logs: list[dict[str, Any]] | None = None,
    assets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return only rules that match every declared constraint."""
    context = [item for item in (alert, proof) if isinstance(item, dict)]
    context.extend(item for item in (endpoint_logs or []) if isinstance(item, dict))
    context.extend(item for item in (assets or []) if isinstance(item, dict))
    flattened = _flatten(context)
    observed_at = _timestamp(context)
    matched: list[dict[str, Any]] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        status = _norm(record.get("status", 1))
        if status not in _ACTIVE_STATUSES:
            continue

        structured = any(key in record for key in ("ruleList", "timeRange", "hostIp", "isHostAll"))
        rules = record.get("ruleList") or []
        time_range = record.get("timeRange") or {}
        if not isinstance(time_range, dict):
            time_range = {}
        if not bool(record.get("isUnlimited")):
            try:
                if time_range.get("start") is not None and observed_at < int(time_range["start"]):
                    continue
                if time_range.get("end") is not None and observed_at > int(time_range["end"]):
                    continue
            except (TypeError, ValueError):
                continue

        host_ip = str(record.get("hostIp") or "").strip()
        if host_ip and not bool(record.get("isHostAll")):
            if _norm(host_ip) not in {_norm(item) for item in _context_values(flattened, "hostIp")}:
                continue

        if structured and not isinstance(rules, list):
            continue
        if structured and isinstance(rules, list) and any(
            not isinstance(rule, dict) or not _rule_matches(rule, flattened) for rule in rules
        ):
            continue
        if structured and not rules and not host_ip and not bool(record.get("isHostAll")):
            continue

        item = copy.deepcopy(record)
        item["matchMeta"] = {
            "matched": True,
            "matchedFields": [str(rule.get("field")) for rule in rules if isinstance(rule, dict)],
            "evaluatedAt": observed_at,
        }
        matched.append(item)
    return matched
