"""kb-r3c：层级 RAG 父 chunk 解析。"""
import asyncio
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.kb_hierarchical_rag import (  # type: ignore[import]
    enrich_kb_hits_with_parent_chunks,
    format_chunk_record_body,
    kb_hierarchical_rag_enabled,
    parent_chunk_refs_from_payload,
)


def test_parent_chunk_refs_from_payload():
    assert parent_chunk_refs_from_payload({}) == ("", "")
    assert parent_chunk_refs_from_payload({"parent_chunk_id": " chk-1 "}) == ("chk-1", "")
    assert parent_chunk_refs_from_payload(
        {"parent_chunk_id": "a", "parent_chunk_task_id": " t1 "}
    ) == ("a", "t1")
    assert parent_chunk_refs_from_payload({"kb_parent_chunk_id": "x"}) == ("x", "")


def test_format_chunk_record_body():
    assert format_chunk_record_body(None, 100) == ""
    rec = {"content": {"body": "hello world"}}
    assert format_chunk_record_body(rec, 100) == "hello world"
    long_body = "x" * 100
    out = format_chunk_record_body({"content": {"body": long_body}}, 20)
    assert out.startswith("xxxxxxxxxxxxxxxxxxxx")
    assert "truncated" in out


@pytest.mark.asyncio
async def test_enrich_kb_hits_resolves_parent(monkeypatch):
    monkeypatch.setenv("KB_HIERARCHICAL_RAG", "true")

    def fake_read(task_id: str, chunk_id: str, **kwargs):
        if chunk_id == "chk-p" and task_id == "task-a":
            return {"content": {"body": "parent blob"}, "meta": {}}
        return None

    monkeypatch.setattr("app.core.chunk_store.read_chunk", fake_read)

    hits = [
        {
            "source": "knowledge",
            "id": "1",
            "parent_chunk_id": "chk-p",
            "parent_chunk_task_id": "",
        }
    ]
    n = await enrich_kb_hits_with_parent_chunks(
        task_id="task-a",
        hit_summaries=hits,
        target_context={},
    )
    assert n == 1
    assert hits[0].get("parent_chunk_preview") == "parent blob"
    assert hits[0].get("parent_chunk_resolved") is True


@pytest.mark.asyncio
async def test_enrich_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("KB_HIERARCHICAL_RAG", "false")
    hits = [{"parent_chunk_id": "chk-p"}]
    n = await enrich_kb_hits_with_parent_chunks(
        task_id="t",
        hit_summaries=hits,
        target_context={},
    )
    assert n == 0
    assert "parent_chunk_preview" not in hits[0]


def test_kb_hierarchical_rag_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("KB_HIERARCHICAL_RAG", "true")
    assert kb_hierarchical_rag_enabled() is True
    monkeypatch.setenv("KB_HIERARCHICAL_RAG", "0")
    assert kb_hierarchical_rag_enabled() is False
