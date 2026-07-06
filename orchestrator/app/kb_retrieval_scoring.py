"""
kb-r3b：检索结果事后加权（phase 对齐 / effectiveness），配合 KB_RETRIEVE_SOFT_SCORING 放宽 Qdrant must。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from app.clients.kb_client import Hit


def kb_retrieve_soft_scoring_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    v = (env.get("KB_RETRIEVE_SOFT_SCORING") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def kb_soft_prefetch_top_k(base_top_k: int, environ: Mapping[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = (env.get("KB_RETRIEVE_SOFT_PREFETCH") or "").strip()
    if raw:
        try:
            return max(1, max(base_top_k, int(raw)))
        except ValueError:
            pass
    return max(1, base_top_k, max(base_top_k * 4, 40))


def kb_experience_read_prefetch_top_k(base_top_k: int, environ: Mapping[str, str] | None = None) -> int:
    """kb-r4a：仅放宽 effectiveness 时，放大 experience 召回上限再加权截断。"""
    env = environ if environ is not None else os.environ
    raw = (env.get("KB_EXPERIENCE_READ_PREFETCH") or "").strip()
    if raw:
        try:
            return max(1, max(base_top_k, int(raw)))
        except ValueError:
            pass
    return max(1, base_top_k, max(base_top_k * 4, 40))


def _env_float(name: str, default: float, environ: Mapping[str, str] | None = None) -> float:
    env = environ if environ is not None else os.environ
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RetrievalScoreWeights:
    phase_match: float
    phase_cross: float
    effectiveness_proven: float
    effectiveness_pending: float
    effectiveness_deprecated: float
    effectiveness_unknown: float


def load_retrieval_score_weights(environ: Mapping[str, str] | None = None) -> RetrievalScoreWeights:
    env = environ if environ is not None else os.environ
    return RetrievalScoreWeights(
        phase_match=_env_float("KB_R3B_PHASE_MATCH", 1.15, env),
        phase_cross=_env_float("KB_R3B_PHASE_CROSS", 0.55, env),
        effectiveness_proven=_env_float("KB_R3B_EFFECTIVENESS_PROVEN", 1.25, env),
        effectiveness_pending=_env_float("KB_R3B_EFFECTIVENESS_PENDING", 0.9, env),
        effectiveness_deprecated=_env_float("KB_R3B_EFFECTIVENESS_DEPRECATED", 0.2, env),
        effectiveness_unknown=_env_float("KB_R3B_EFFECTIVENESS_UNKNOWN", 1.0, env),
    )


def _norm_phase(s: str) -> str:
    return (s or "").strip().upper()


def phase_score_multiplier(hit_phase: Any, current_phase: str, w: RetrievalScoreWeights) -> float:
    hp = _norm_phase(str(hit_phase or ""))
    cp = _norm_phase(current_phase or "")
    if not hp:
        return 1.0
    if cp and hp == cp:
        return w.phase_match
    return w.phase_cross


def effectiveness_score_multiplier(raw: Any, w: RetrievalScoreWeights) -> float:
    e = str(raw or "").strip().lower()
    if e == "proven":
        return w.effectiveness_proven
    if e == "pending":
        return w.effectiveness_pending
    if e == "deprecated":
        return w.effectiveness_deprecated
    return w.effectiveness_unknown


def apply_soft_retrieval_scoring(
    hits: list[Hit],
    *,
    current_phase: str,
    source: str,
    weights: RetrievalScoreWeights | None = None,
) -> list[Hit]:
    """
    对 Hit.score 乘 phase（payload.phase）与 experience 的 effectiveness 权重，降序排序。
    knowledge 源仅乘 phase（无 phase 字段则因子为 1）。
    """
    w = weights or load_retrieval_score_weights()
    out: list[Hit] = []
    for h in hits:
        pl = dict(h.payload) if isinstance(h.payload, dict) else {}
        base = float(h.score) if h.score is not None else 0.0
        pf = phase_score_multiplier(pl.get("phase"), current_phase, w)
        if (source or "").strip().lower() == "experience":
            ef = effectiveness_score_multiplier(pl.get("effectiveness"), w)
        else:
            ef = 1.0
        combined = base * pf * ef
        pl["_kb_r3b_phase_factor"] = pf
        pl["_kb_r3b_effectiveness_factor"] = ef
        pl["_kb_r3b_vector_score"] = base
        pl["_kb_r3b_source"] = source
        out.append(
            Hit(
                id=h.id,
                collection=h.collection,
                score=combined,
                snippet=h.snippet,
                payload=pl,
            )
        )
    out.sort(key=lambda x: -(x.score or 0.0))
    return out


def truncate_top_k(hits: list[Hit], top_k: int) -> list[Hit]:
    if top_k < 1:
        return []
    return hits[:top_k]
