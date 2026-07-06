"""kb-r3a：KB 检索 query 净化。"""
from pathlib import Path

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.enums import Phase  # type: ignore[import]
from app.clients.llm_client import _build_kb_query_and_filters  # type: ignore[import]
from app.kb_query_purify import (  # type: ignore[import]
    build_purified_kb_embed_query_text,
    embed_target_hint,
    kb_query_purify_enabled,
    purify_free_text_for_embedding,
    redact_ips_and_urls,
)


def test_redact_ips_and_urls():
    s = redact_ips_and_urls("see http://evil.com/x and 10.0.0.1")
    assert "[url]" in s
    assert "[ip]" in s
    assert "10.0.0.1" not in s
    assert "evil.com" not in s


def test_embed_target_hint():
    assert "scheme=https" in embed_target_hint("https://a.example/path")
    assert embed_target_hint("203.0.113.7") == "target_kind=ipv4"
    assert embed_target_hint("api.internal.corp") == "target_kind=hostname"


def test_purify_truncates_history():
    long_h = "x" * 2000
    out = purify_free_text_for_embedding(long_h, max_chars=100)
    assert len(out) <= 120
    assert "truncated" in out


def test_build_purified_includes_skills_not_raw_target():
    q = build_purified_kb_embed_query_text(
        phase="RECON",
        target_raw="https://secret.internal:8443/foo",
        todo_name="",
        todo_desc="",
        history_summary="",
        available_skill_ids=["nmap", "nuclei"],
        environ={},
    )
    assert "phase=RECON" in q
    assert "target_scheme=https" in q
    assert "secret.internal" not in q
    assert "available_skills=nmap,nuclei" in q


def test_build_kb_query_legacy_vs_purify(monkeypatch):
    monkeypatch.setenv("KB_QUERY_PURIFY", "false")
    ctx = {"target": "https://x.example/a", "_current_todo": {"id": "1", "name": "n", "description": "d"}}
    q_legacy, kf, ef = _build_kb_query_and_filters(
        phase=Phase.RECON,
        target_context=ctx,
        history_summary="hit 192.168.1.1",
        available_skill_ids=["katana"],
    )
    assert "x.example" in q_legacy
    assert "192.168.1.1" in q_legacy

    monkeypatch.setenv("KB_QUERY_PURIFY", "true")
    q_p, kf2, ef2 = _build_kb_query_and_filters(
        phase=Phase.RECON,
        target_context=ctx,
        history_summary="hit 192.168.1.1",
        available_skill_ids=["katana"],
    )
    assert kf == kf2 and ef == ef2
    assert "x.example" not in q_p
    assert "192.168.1.1" not in q_p
    assert "[ip]" in q_p or "target_scheme=https" in q_p


def test_kb_query_purify_disabled(monkeypatch):
    monkeypatch.setenv("KB_QUERY_PURIFY", "false")
    assert kb_query_purify_enabled() is False
