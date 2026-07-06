"""
kb-r2a：静态知识 Manual / CVE / Blogs 分档 boost 与合并排序。

- **split**：按三个 Qdrant collection 分别检索后合并再按 boost 重排。
- **unified**：单集合检索，依据 payload `kb_tier` 调整得分（需 prefetch 再重排）。
"""

from __future__ import annotations

from typing import Any, Mapping

from app.clients.kb_client import Hit


def normalize_kb_tier(raw: Any) -> str:
    """将 payload 中的 kb_tier 规范为 manual | cve | blogs | unknown。"""
    if raw is None:
        return "unknown"
    k = str(raw).strip().lower().replace("-", "_")
    aliases = {
        "manual": "manual",
        "kb_manual": "manual",
        "manuals": "manual",
        "cve": "cve",
        "kb_cve": "cve",
        "blog": "blogs",
        "blogs": "blogs",
        "kb_blogs": "blogs",
    }
    return aliases.get(k, k if k in ("manual", "cve", "blogs") else "unknown")


def boost_map_from_config(
    *,
    boost_manual: float,
    boost_cve: float,
    boost_blogs: float,
) -> dict[str, float]:
    return {
        "manual": boost_manual,
        "cve": boost_cve,
        "blogs": boost_blogs,
        "unknown": 1.0,
    }


def merge_tagged_hits_with_boost(
    tagged: list[tuple[Hit, str]],
    *,
    boosts: Mapping[str, float],
    top_k: int,
) -> list[Hit]:
    """
    对 (Hit, tier) 列表应用 boost、按 id 去重保留更高 effective score，截断 top_k。
    Hit.score 替换为 effective；payload 写入 kb_tier、_kb_vector_score、_kb_effective_score。
    """
    if top_k < 1:
        return []

    def eff_score(h: Hit, tier: str) -> float:
        b = float(boosts.get(tier, 1.0))
        base = float(h.score) if h.score is not None else 0.0
        return base * b

    best: dict[str, tuple[Hit, str, float, float]] = {}
    for i, (h, tier) in enumerate(tagged):
        tid = (h.id or "").strip()
        if not tid:
            tid = f"_:{i}:{h.collection}"
        vec = float(h.score) if h.score is not None else 0.0
        eff = eff_score(h, tier)
        prev = best.get(tid)
        if prev is None or eff > prev[3]:
            best[tid] = (h, tier, vec, eff)

    rebuilt: list[Hit] = []
    for h, tier, vec, eff in best.values():
        pl = dict(h.payload) if isinstance(h.payload, dict) else {}
        pl["kb_tier"] = tier
        pl["_kb_vector_score"] = vec
        pl["_kb_effective_score"] = eff
        rebuilt.append(
            Hit(
                id=h.id,
                collection=h.collection,
                score=eff,
                snippet=h.snippet,
                payload=pl,
            )
        )

    rebuilt.sort(key=lambda x: -(x.score or 0.0))
    return rebuilt[:top_k]
