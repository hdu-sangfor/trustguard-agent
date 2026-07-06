"""FinOps：解析执行器回传的 token usage，累计成本，并与 ORCH_PHASE_COST_BUDGET_USD 比较。"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Tuple


def phase_cost_budget_usd_from_env() -> Optional[float]:
    """未配置或 <=0 时关闭成本熔断。"""
    raw = (os.getenv("ORCH_PHASE_COST_BUDGET_USD") or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None


def _price_per_million_tokens(env_name: str, default: float) -> float:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def finops_input_usd_per_1m() -> float:
    return _price_per_million_tokens("ORCH_FINOPS_INPUT_USD_PER_1M", 0.0)


def finops_output_usd_per_1m() -> float:
    return _price_per_million_tokens("ORCH_FINOPS_OUTPUT_USD_PER_1M", 0.0)


def parse_token_counts(usage: Any) -> Tuple[int, int]:
    """
    从执行器 `usage` 解析 (input_tokens, output_tokens)，兼容 Anthropic / OpenAI 常见键名。
    """
    if not isinstance(usage, Mapping):
        return 0, 0

    def _nz_int(x: Any) -> int:
        try:
            v = int(x)
            return v if v > 0 else 0
        except (TypeError, ValueError):
            return 0

    inp = _nz_int(usage.get("input_tokens")) or _nz_int(usage.get("prompt_tokens"))
    out = _nz_int(usage.get("output_tokens")) or _nz_int(usage.get("completion_tokens"))
    if inp == 0 and out == 0:
        t = _nz_int(usage.get("total_tokens"))
        if t:
            inp = t
    return inp, out


def merge_usage_sources(exec_result: Any, resolved_artifacts: Mapping[str, Any] | None) -> Optional[dict[str, Any]]:
    """优先顶层 ExecuteSkillResponse.usage，其次 parsed_artifacts.usage。"""
    u = getattr(exec_result, "usage", None) if exec_result is not None else None
    if isinstance(u, Mapping):
        return dict(u)
    ra = resolved_artifacts or {}
    u2 = ra.get("usage")
    if isinstance(u2, Mapping):
        return dict(u2)
    return None


def cost_usd_for_tokens(inp: int, out: int) -> float:
    pin, pout = finops_input_usd_per_1m(), finops_output_usd_per_1m()
    return (max(0, inp) / 1_000_000.0) * pin + (max(0, out) / 1_000_000.0) * pout


def accumulate_finops_usage_mapping_into_state(state: Any, usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    将单次 LLM usage 映射累加到 TaskState（llm_*_tokens_total / cumulative_cost_usd）。
    返回可写入 Trace 的 finops_delta（无增量时为空 dict）。
    """
    if not isinstance(usage, Mapping):
        return {}
    inp, outp = parse_token_counts(usage)
    if inp <= 0 and outp <= 0:
        return {}
    delta_cost = cost_usd_for_tokens(inp, outp)
    state.llm_input_tokens_total = int(getattr(state, "llm_input_tokens_total", 0) or 0) + inp
    state.llm_output_tokens_total = int(getattr(state, "llm_output_tokens_total", 0) or 0) + outp
    state.cumulative_cost_usd = float(getattr(state, "cumulative_cost_usd", 0.0) or 0.0) + float(delta_cost)
    return {
        "delta_input_tokens": inp,
        "delta_output_tokens": outp,
        "delta_cost_usd": round(float(delta_cost), 12),
        "cumulative_input_tokens": int(state.llm_input_tokens_total or 0),
        "cumulative_output_tokens": int(state.llm_output_tokens_total or 0),
        "cumulative_cost_usd": round(float(state.cumulative_cost_usd or 0.0), 12),
    }
