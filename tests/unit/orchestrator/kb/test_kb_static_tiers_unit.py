"""kb-r2a：静态知识 kb_tier 规范化与 boost 合并排序。"""
from pathlib import Path

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients.kb_client import Hit  # type: ignore[import]
from app.kb_static_tiers import (  # type: ignore[import]
    boost_map_from_config,
    merge_tagged_hits_with_boost,
    normalize_kb_tier,
)


def test_normalize_kb_tier():
    assert normalize_kb_tier("manual") == "manual"
    assert normalize_kb_tier("KB_MANUAL") == "manual"
    assert normalize_kb_tier("kb_cve") == "cve"
    assert normalize_kb_tier("blogs") == "blogs"
    assert normalize_kb_tier(None) == "unknown"
    assert normalize_kb_tier("nope") == "unknown"


def test_merge_boost_prefers_higher_effective_and_truncates():
    boosts = boost_map_from_config(boost_manual=10.0, boost_cve=1.0, boost_blogs=1.0)
    h1 = Hit(id="a", collection="c", score=0.5, snippet="", payload={})
    h2 = Hit(id="b", collection="c", score=0.9, snippet="", payload={})
    tagged = [(h1, "manual"), (h2, "cve")]
    out = merge_tagged_hits_with_boost(tagged, boosts=boosts, top_k=1)
    assert len(out) == 1
    # manual 0.5 * 10 = 5.0 > cve 0.9 * 1.2? 0.9*1.2=1.08, still manual wins
    assert out[0].id == "a"
    assert abs((out[0].score or 0) - 5.0) < 1e-6
    pl = out[0].payload or {}
    assert pl.get("kb_tier") == "manual"
    assert pl.get("_kb_vector_score") == 0.5


def test_merge_dedupes_by_id_keeps_best_effective():
    boosts = boost_map_from_config(boost_manual=1.0, boost_cve=2.0, boost_blogs=1.0)
    h = Hit(id="x", collection="c", score=0.4, snippet="", payload={})
    tagged = [(h, "manual"), (h, "cve")]
    out = merge_tagged_hits_with_boost(tagged, boosts=boosts, top_k=5)
    assert len(out) == 1
    assert out[0].id == "x"
    assert abs((out[0].score or 0) - 0.8) < 1e-6  # 0.4 * 2.0 (cve boost)
    assert (out[0].payload or {}).get("kb_tier") == "cve"
