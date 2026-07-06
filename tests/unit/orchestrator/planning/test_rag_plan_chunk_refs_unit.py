"""kb-r5a：RAG 命中合并进 PlanItem.context_chunk_refs。"""
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core.rag_plan_chunk_refs import (  # type: ignore[import]
    merge_kb_hits_into_plan_chunk_refs,
    rag_merge_chunk_refs_enabled,
)
from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem, PlanList  # type: ignore[import]


def _item(skill_id: str = "nmap", refs: list | None = None) -> PlanItem:
    return PlanItem(
        plan_id="p1",
        task_id="t1",
        skill_id=skill_id,
        plan_content="scan",
        context_chunk_refs=refs or [],
        constraints=PlanConstraints(target_scope="host:example.com", timeout_seconds=60),
    )


def test_rag_merge_chunk_refs_enabled_default_and_off():
    assert rag_merge_chunk_refs_enabled({"ORCH_RAG_MERGE_CHUNK_REFS": "true"}) is True
    assert rag_merge_chunk_refs_enabled({"ORCH_RAG_MERGE_CHUNK_REFS": "false"}) is False
    assert rag_merge_chunk_refs_enabled({"ORCH_RAG_MERGE_CHUNK_REFS": "off"}) is False


def test_merge_adds_experience_chunk_for_matching_skill():
    pl = PlanList(task_id="t1", items=[_item()])
    hits = [
        {"source": "experience", "skill_id": "nmap", "chunk_id": "chk-abc", "workspace_id": "ws1"},
    ]
    out = merge_kb_hits_into_plan_chunk_refs(
        pl,
        hits,
        available_skill_ids=["nmap"],
        skill_aliases={},
        max_refs_per_item=32,
    )
    refs = out.items[0].context_chunk_refs
    assert len(refs) == 1
    assert refs[0].chunk_id == "chk-abc"
    assert refs[0].tenant_id == "ws1"


def test_merge_skips_knowledge_source():
    pl = PlanList(task_id="t1", items=[_item()])
    hits = [{"source": "knowledge", "skill_id": "nmap", "chunk_id": "chk-x"}]
    out = merge_kb_hits_into_plan_chunk_refs(
        pl,
        hits,
        available_skill_ids=["nmap"],
        skill_aliases={},
    )
    assert out.items[0].context_chunk_refs == []


def test_merge_dedupes_chunk_id_and_preserves_existing():
    existing = [ContextChunkRef(chunk_id="chk-old")]
    pl = PlanList(task_id="t1", items=[_item(refs=existing)])
    hits = [
        {"source": "experience", "skill_id": "nmap", "chunk_id": "chk-old"},
        {"source": "experience", "skill_id": "nmap", "chunk_id": "chk-new"},
    ]
    out = merge_kb_hits_into_plan_chunk_refs(
        pl,
        hits,
        available_skill_ids=["nmap"],
        skill_aliases={},
        max_refs_per_item=32,
    )
    ids = [r.chunk_id for r in out.items[0].context_chunk_refs]
    assert ids == ["chk-old", "chk-new"]


def test_merge_respects_cap():
    pl = PlanList(task_id="t1", items=[_item()])
    hits = [
        {"source": "experience", "skill_id": "nmap", "chunk_id": "chk-a"},
        {"source": "experience", "skill_id": "nmap", "chunk_id": "chk-b"},
    ]
    out = merge_kb_hits_into_plan_chunk_refs(
        pl,
        hits,
        available_skill_ids=["nmap"],
        skill_aliases={},
        max_refs_per_item=1,
    )
    assert len(out.items[0].context_chunk_refs) == 1
    assert out.items[0].context_chunk_refs[0].chunk_id == "chk-a"


def test_merge_skill_alias_match():
    pl = PlanList(task_id="t1", items=[_item(skill_id="web-fingerprint")])
    hits = [{"source": "experience", "skill_id": "whatweb-fingerprint", "chunk_id": "chk-w"}]
    aliases = {"web-fingerprint": "whatweb-fingerprint"}
    out = merge_kb_hits_into_plan_chunk_refs(
        pl,
        hits,
        available_skill_ids=["whatweb-fingerprint"],
        skill_aliases=aliases,
    )
    assert [r.chunk_id for r in out.items[0].context_chunk_refs] == ["chk-w"]


def test_merge_disabled_via_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCH_RAG_MERGE_CHUNK_REFS", "false")
    pl = PlanList(task_id="t1", items=[_item()])
    hits = [{"source": "experience", "skill_id": "nmap", "chunk_id": "chk-z"}]
    out = merge_kb_hits_into_plan_chunk_refs(
        pl,
        hits,
        available_skill_ids=["nmap"],
        skill_aliases={},
    )
    assert out.items[0].context_chunk_refs == []
