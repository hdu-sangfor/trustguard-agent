"""kb-r1d：legacy experience snippet、双读合并、legacy collection 过滤构造。"""
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients.kb_client import Hit, merge_experience_hits  # type: ignore[import]
from app.kb_experience_payload import (  # type: ignore[import]
    build_experience_legacy_collection_retrieve_filters,
    experience_payload_snippet_for_display,
    is_legacy_experience_payload,
)
from app.models import Phase  # type: ignore[import]


def test_is_legacy_missing_schema():
    assert is_legacy_experience_payload({"skill_id": "nmap"}) is True
    assert is_legacy_experience_payload({"schema_version": "kb-exp-v1"}) is False


def test_legacy_snippet_truncates_long_summary():
    huge = "x" * 5000
    s = experience_payload_snippet_for_display(
        {"skill_id": "nuclei", "phase": "EXPLOIT", "summary": huge},
    )
    assert "[legacy-experience]" in s
    assert "legacy summary truncated" in s
    assert len(s) < len(huge)


def test_modern_snippet_prefers_summary():
    s = experience_payload_snippet_for_display(
        {
            "schema_version": "kb-exp-v1",
            "summary": "short ok",
            "context_snapshot": {"blob": "y" * 9000},
        },
    )
    assert s == "short ok"


def test_modern_snippet_kb_features_intent_projection():
    s = experience_payload_snippet_for_display(
        {
            "schema_version": "kb-exp-v1",
            "summary": "",
            "context_snapshot": {"kb_features": {"intent_projection": "ports: 80,443"}},
        },
    )
    assert "ports:" in s


def test_legacy_snippet_skips_context_snapshot_blob():
    s = experience_payload_snippet_for_display(
        {
            "skill_id": "x",
            "phase": "RECON",
            "summary": "",
            "context_snapshot": {"artifacts": "y" * 9000},
        },
    )
    assert "[legacy-experience]" in s
    assert "artifacts" not in s


def test_merge_experience_hits_dedupes_by_artifact_ref():
    a = Hit(
        id="1",
        collection="c",
        score=0.5,
        snippet="",
        payload={"artifact_ref": "ref-1", "schema_version": "kb-exp-v1"},
    )
    b = Hit(
        id="2",
        collection="c",
        score=0.9,
        snippet="",
        payload={"artifact_ref": "ref-1"},
    )
    out = merge_experience_hits([a], [b], top_k=5)
    assert len(out) == 1
    assert out[0].id == "2"


def test_build_experience_legacy_collection_retrieve_filters_no_workspace(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KB_EXPERIENCE_EFFECTIVENESS_SOFT", "false")
    fl = build_experience_legacy_collection_retrieve_filters(phase=Phase.RECON, todo_id="")
    assert fl == {"phase": "RECON", "effectiveness": "proven"}
    fl2 = build_experience_legacy_collection_retrieve_filters(phase=Phase.RECON, todo_id="t1")
    assert fl2["todo_id"] == "t1"
    assert "workspace_id" not in fl2


def test_build_experience_legacy_filters_kb_r4a_omits_proven(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KB_EXPERIENCE_EFFECTIVENESS_SOFT", "true")
    fl = build_experience_legacy_collection_retrieve_filters(phase=Phase.RECON, todo_id="")
    assert fl == {"phase": "RECON"}
    assert "effectiveness" not in fl
