"""
技能参数规范化：在预检最早期统一数据契约，避免脆弱字符串切割。

Nuclei tags 在编排器内统一为 list[str]；仅在执行器拼 CLI 时再 join。
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any


def dedupe_tags_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in items:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def normalize_tags_to_list(raw: Any) -> list[str]:
    """
    将 LLM/上游可能输出的 tags 统一为 list[str]。
    兼容：None、list、tuple、set、逗号分隔字符串、JSON 数组字符串、Python repr 列表字符串。
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[str] = []
        for x in raw:
            if isinstance(x, str):
                s = x.strip()
                if s:
                    out.append(s)
            elif x is not None and not isinstance(x, (dict, list)):
                s = str(x).strip()
                if s:
                    out.append(s)
        return dedupe_tags_preserve_order(out)
    if isinstance(raw, (tuple, set)):
        return normalize_tags_to_list(list(raw))
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("[") and "]" in s:
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple)):
                    return normalize_tags_to_list(list(parsed))
            except (ValueError, SyntaxError, TypeError):
                pass
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return normalize_tags_to_list(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        # 逗号 / 空白分隔（仅作输入清洗，非 IML 钳制逻辑）
        parts = [p.strip() for p in re.split(r"[\s,]+", s) if p.strip()]
        return dedupe_tags_preserve_order(parts)
    return normalize_tags_to_list(str(raw))


def tags_list_to_nuclei_cli(tags: list[str] | None) -> str:
    """执行器侧拼 nuclei -tags 时使用。"""
    if not tags:
        return ""
    return ",".join(str(t).strip() for t in tags if str(t).strip())
