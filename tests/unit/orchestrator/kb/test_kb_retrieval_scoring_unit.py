"""kb-r3b: post-retrieval scoring."""
from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients.kb_client import Hit  # type: ignore[import]
from app.kb_retrieval_scoring import (  # type: ignore[import]
    RetrievalScoreWeights,
    apply_soft_retrieval_scoring,
    effectiveness_score_multiplier,
    kb_experience_read_prefetch_top_k,
    kb_retrieve_soft_scoring_enabled,
    kb_soft_prefetch_top_k,
    phase_score_multiplier,
    truncate_top_k,
)


def test_phase_multiplier_missing_payload_phase_is_neutral():
    w = RetrievalScoreWeights(1.2, 0.5, 1.0, 1.0, 1.0, 1.0)
    assert phase_score_multiplier(None, "RECON", w) == 1.0


def test_phase_multiplier_match_vs_cross():
    w = RetrievalScoreWeights(2.0, 0.25, 1.0, 1.0, 1.0, 1.0)
    assert phase_score_multiplier("RECON", "RECON", w) == 2.0
    assert phase_score_multiplier("EXPLOIT", "RECON", w) == 0.25


def test_effectiveness_multipliers():
    w = RetrievalScoreWeights(1.0, 1.0, 10.0, 2.0, 0.1, 3.0)
    assert effectiveness_score_multiplier("proven", w) == 10.0
    assert effectiveness_score_multiplier("pending", w) == 2.0
    assert effectiveness_score_multiplier("deprecated", w) == 0.1
    assert effectiveness_score_multiplier("", w) == 3.0


def test_apply_soft_retrieval_scoring_fixture_order():
    w = RetrievalScoreWeights(
        phase_match=2.0,
        phase_cross=0.5,
        effectiveness_proven=2.0,
        effectiveness_pending=0.8,
        effectiveness_deprecated=0.1,
        effectiveness_unknown=1.0,
    )
    h_cross_pending = Hit(
        id="a",
        collection="exp",
        score=1.0,
        snippet="",
        payload={"phase": "RECON", "effectiveness": "pending"},
    )
    h_match_proven = Hit(
        id="b",
        collection="exp",
        score=1.0,
        snippet="",
        payload={"phase": "EXPLOIT", "effectiveness": "proven"},
    )
    out = apply_soft_retrieval_scoring(
        [h_cross_pending, h_match_proven],
        current_phase="EXPLOIT",
        source="experience",
        weights=w,
    )
    assert out[0].id == "b"
    assert out[1].id == "a"
    assert (out[0].payload or {}).get("_kb_r3b_vector_score") == 1.0


def test_knowledge_source_skips_effectiveness_factor():
    w = RetrievalScoreWeights(1.5, 0.6, 9.0, 1.0, 1.0, 1.0)
    h = Hit(
        id="k",
        collection="know",
        score=1.0,
        snippet="",
        payload={"phase": "RECON", "effectiveness": "pending"},
    )
    out = apply_soft_retrieval_scoring([h], current_phase="RECON", source="knowledge", weights=w)
    assert abs((out[0].score or 0) - 1.5) < 1e-6
    assert (out[0].payload or {}).get("_kb_r3b_effectiveness_factor") == 1.0


def test_truncate_top_k():
    hs = [Hit(id=str(i), collection="c", score=float(i), snippet="", payload={}) for i in range(5)]
    assert len(truncate_top_k(hs, 2)) == 2


def test_kb_soft_prefetch_and_flag(monkeypatch):
    monkeypatch.setenv("KB_RETRIEVE_SOFT_PREFETCH", "99")
    assert kb_soft_prefetch_top_k(5) == 99
    monkeypatch.setenv("KB_RETRIEVE_SOFT_SCORING", "true")
    assert kb_retrieve_soft_scoring_enabled() is True


def test_kb_experience_read_prefetch(monkeypatch):
    monkeypatch.setenv("KB_EXPERIENCE_READ_PREFETCH", "88")
    assert kb_experience_read_prefetch_top_k(3) == 88
