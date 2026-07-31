"""kb-r1d：legacy experience snippet、双读合并、legacy collection 过滤构造。"""

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients.kb_client import (  # type: ignore[import]
    Hit,
    get_kb_config,
    merge_experience_hits,
)
from app.clients.llm_client import (  # type: ignore[import]
    _assemble_decision_user_content,
)
from app.kb_experience_payload import (  # type: ignore[import]
    build_experience_legacy_collection_retrieve_filters,
    experience_payload_snippet_for_display,
    is_legacy_experience_payload,
)
from app.models import Phase  # type: ignore[import]


def test_legacy_read_switches_default_to_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LEGACY_STATIC_KB_READ_ENABLED", raising=False)
    monkeypatch.delenv("LEGACY_EXPERIENCE_READ_ENABLED", raising=False)

    cfg = get_kb_config()

    assert cfg.legacy_static_read_enabled is True
    assert cfg.legacy_experience_read_enabled is True


def test_legacy_static_read_can_be_disabled_without_disabling_experience(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LEGACY_STATIC_KB_READ_ENABLED", "false")
    monkeypatch.setenv("LEGACY_EXPERIENCE_READ_ENABLED", "true")

    cfg = get_kb_config()

    assert cfg.legacy_static_read_enabled is False
    assert cfg.legacy_experience_read_enabled is True


@pytest.mark.asyncio
async def test_decision_retrieval_skips_static_but_keeps_experience(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.clients import kb_client as kb_module
    from app.clients import trace_client

    monkeypatch.setenv("KB_ENABLED", "true")
    monkeypatch.setenv("LEGACY_STATIC_KB_READ_ENABLED", "false")
    monkeypatch.setenv("LEGACY_EXPERIENCE_READ_ENABLED", "true")
    monkeypatch.setenv("KNOWLEDGE_MCP_ENABLED", "false")
    monkeypatch.setenv("KB_RETRIEVE_SOFT_SCORING", "false")
    monkeypatch.setenv("KB_EXPERIENCE_EFFECTIVENESS_SOFT", "false")
    monkeypatch.delenv("KB_EXPERIENCE_LEGACY_COLLECTION", raising=False)

    class FakeKBClient:
        static_calls = 0
        experience_calls = 0

        async def retrieve_knowledge_tiered(self, **kwargs):
            self.static_calls += 1
            raise AssertionError("legacy static retrieval must be disabled")

        async def retrieve(self, **kwargs):
            self.experience_calls += 1
            return [
                Hit(
                    id="experience-1",
                    collection=str(kwargs["collection"]),
                    score=0.9,
                    snippet="已验证的历史侦察经验",
                    payload={
                        "effectiveness": "proven",
                        "skill_id": "nmap",
                    },
                )
            ]

    async def ignore_trace(event):
        return None

    fake = FakeKBClient()
    monkeypatch.setattr(kb_module, "_KB_CLIENT", fake)
    monkeypatch.setattr(trace_client, "emit_trace", ignore_trace)
    target_context = {
        "target": "http://example.test",
        "_current_todo": {"id": "todo-1", "name": "端口侦察"},
    }

    await _assemble_decision_user_content(
        task_id="task-read-switch",
        phase=Phase.RECON,
        target_context=target_context,
        history_summary="",
        available_skill_ids=["nmap"],
        summary_chunks=None,
        user_intro="test",
    )

    assert fake.static_calls == 0
    assert fake.experience_calls == 1
    assert [hit["source"] for hit in target_context["kb_hits"]] == ["experience"]


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
