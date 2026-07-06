"""
将分散在 target_context 各 skill 前缀键下的信号，合并为稳定的 pipeline_* 标准键，
供 PolicyEngine / Nuclei 狙击 / Manager Todo 与后续模块统一读取，避免键名碎片化。

合并逻辑复用 manager_agent 的 collect_*（与 Todo 信号源一致）。
"""
from __future__ import annotations

from typing import Any

from app.core.manager_agent import collect_suspicious_signal_items, collect_tech_stack_evidence_items


def normalize_pipeline_context(ctx: dict[str, Any]) -> None:
    """
    就地写入：
      - pipeline_tech_stack_evidence: list[dict]
      - pipeline_suspicious_signals: list[dict]
      - pipeline_signal_urls: list[str]（suspicious URL + high_value 端点 URL 去重保序）
    """
    if not isinstance(ctx, dict):
        return

    tech = collect_tech_stack_evidence_items(ctx)
    susp = collect_suspicious_signal_items(ctx)

    seen: set[str] = set()
    urls: list[str] = []

    for item in susp:
        if not isinstance(item, dict):
            continue
        u = str(item.get("url") or "").strip()
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            urls.append(u)

    for key in ("dispatcher_high_value_endpoints", "high_value_endpoints"):
        hv = ctx.get(key)
        if not isinstance(hv, list):
            continue
        for u in hv:
            if isinstance(u, str):
                u = u.strip()
                if u.startswith(("http://", "https://")) and u not in seen:
                    seen.add(u)
                    urls.append(u)

    ctx["pipeline_tech_stack_evidence"] = tech[:120]
    ctx["pipeline_suspicious_signals"] = susp[:50]
    ctx["pipeline_signal_urls"] = urls[:240]
