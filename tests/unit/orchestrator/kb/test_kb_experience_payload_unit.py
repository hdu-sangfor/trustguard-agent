import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.kb_experience_payload import (  # type: ignore[import]
    ExperiencePayload,
    build_experience_retrieve_filters,
    build_experience_retrieve_filters_soft,
    build_intent_embed_text,
    experience_payload_dict_for_upsert,
    pick_workspace_scope_from_context,
    stable_experience_artifact_chunk_id,
)


@dataclass
class _FakeCandidate:
    task_id: str
    phase: str
    skill_id: str
    todo_id: str | None
    agent_role: str | None
    target: str
    artifact_ref: str
    event_id: str | None
    memory_path: str | None
    summary_text: str
    context_snapshot: dict | None
    effectiveness: str
    source: str = "runtime_experience"
    workspace_id: str | None = None
    project_id: str | None = None
    chunk_id: str | None = None
    intent_embed_text: str | None = None


def test_pick_workspace_from_context():
    ws, pr = pick_workspace_scope_from_context(
        {"workspace_id": " ws1 ", "project_id": "p9"},
        include_env_defaults=False,
    )
    assert ws == "ws1"
    assert pr == "p9"


def test_pick_workspace_env_defaults(monkeypatch):
    monkeypatch.setenv("ORCH_KB_DEFAULT_WORKSPACE_ID", "env-ws")
    monkeypatch.delenv("ORCH_KB_DEFAULT_PROJECT_ID", raising=False)
    ws, pr = pick_workspace_scope_from_context({}, include_env_defaults=True)
    assert ws == "env-ws"
    assert pr is None


def test_build_experience_filters_tenant_off(monkeypatch):
    monkeypatch.setenv("KB_EXPERIENCE_TENANT_FILTER", "off")
    monkeypatch.setenv("KB_EXPERIENCE_EFFECTIVENESS_SOFT", "false")
    class P:
        value = "RECON"

    fl = build_experience_retrieve_filters(phase=P(), target_context={"workspace_id": "w"}, todo_id="")
    assert fl == {"phase": "RECON", "effectiveness": "proven"}


def test_build_experience_filters_adds_workspace(monkeypatch):
    monkeypatch.delenv("KB_EXPERIENCE_TENANT_FILTER", raising=False)
    monkeypatch.setenv("KB_EXPERIENCE_TENANT_DEFAULTS", "false")
    monkeypatch.setenv("KB_EXPERIENCE_EFFECTIVENESS_SOFT", "false")

    class P:
        value = "RECON"

    fl = build_experience_retrieve_filters(
        phase=P(),
        target_context={"workspace_id": "w1", "project_id": "p1"},
        todo_id="t1",
    )
    assert fl["phase"] == "RECON"
    assert fl["effectiveness"] == "proven"
    assert fl["todo_id"] == "t1"
    assert fl["workspace_id"] == "w1"
    assert fl["project_id"] == "p1"


def test_experience_payload_dict_roundtrip():
    c = _FakeCandidate(
        task_id="tid",
        phase="RECON",
        skill_id="nmap",
        todo_id=None,
        agent_role=None,
        target="http://ex.com",
        artifact_ref="ref1",
        event_id=None,
        memory_path=None,
        summary_text="s",
        context_snapshot={"a": 1},
        effectiveness="proven",
        workspace_id="w",
        project_id=None,
    )
    d = experience_payload_dict_for_upsert(c, now_iso="2026-01-01T00:00:00Z")
    assert d["kind"] == "experience"
    assert d["schema_version"] == "kb-exp-v1"
    assert d["workspace_id"] == "w"
    assert "project_id" not in d
    assert d["target"] == "http://ex.com"
    ExperiencePayload.model_validate(d)


def test_stable_experience_chunk_id_is_deterministic():
    a = stable_experience_artifact_chunk_id("ref://x")
    b = stable_experience_artifact_chunk_id("ref://x")
    assert a == b
    assert a.startswith("chk-")


def test_build_intent_embed_truncates():
    s = build_intent_embed_text(
        phase="RECON",
        skill_id="nmap",
        target="http://t.com",
        headline="line1\nline2\nignored",
        max_len=80,
    )
    assert "line1" in s
    assert "line2" not in s
    assert len(s) <= 80


def test_experience_payload_includes_chunk_id():
    c = _FakeCandidate(
        task_id="tid",
        phase="RECON",
        skill_id="nmap",
        todo_id=None,
        agent_role=None,
        target="http://ex.com",
        artifact_ref="ref1",
        event_id=None,
        memory_path=None,
        summary_text="long summary " * 50,
        context_snapshot={},
        effectiveness="proven",
        chunk_id="chk-abcdef0123456789abcdef01",
        intent_embed_text="short intent",
    )
    d = experience_payload_dict_for_upsert(c, now_iso="2026-01-01T00:00:00Z")
    assert d["chunk_id"] == "chk-abcdef0123456789abcdef01"
    assert len(d["summary"]) > 20


def test_experience_payload_rejects_bad_effectiveness():
    with pytest.raises(Exception):
        ExperiencePayload(
            skill_id="x",
            phase="RECON",
            task_id="t",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            artifact_ref="r",
            effectiveness="bogus",
        )


def test_build_experience_retrieve_filters_soft_omits_phase_and_effectiveness(monkeypatch):
    monkeypatch.delenv("KB_EXPERIENCE_TENANT_FILTER", raising=False)
    fl = build_experience_retrieve_filters_soft(target_context={}, todo_id="")
    assert "phase" not in fl
    assert "effectiveness" not in fl


def test_build_experience_filters_default_kb_r4a_omits_effectiveness(monkeypatch):
    monkeypatch.setenv("KB_EXPERIENCE_EFFECTIVENESS_SOFT", "true")
    monkeypatch.setenv("KB_EXPERIENCE_TENANT_FILTER", "off")
    class P:
        value = "VULN_SCAN"

    fl = build_experience_retrieve_filters(phase=P(), target_context={}, todo_id="")
    assert fl == {"phase": "VULN_SCAN"}
    assert "effectiveness" not in fl
