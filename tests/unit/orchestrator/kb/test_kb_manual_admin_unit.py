"""kb-r2b：Manual 录入 API 与检索侧 pending 过滤。"""
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients.kb_client import knowledge_hit_visible_for_retrieval  # type: ignore[import]


def test_knowledge_hit_visible_for_retrieval_pending_hidden():
    assert not knowledge_hit_visible_for_retrieval({"review_status": "pending"})
    assert not knowledge_hit_visible_for_retrieval({"review_status": "PENDING"})


def test_knowledge_hit_visible_for_retrieval_approved_and_legacy():
    assert knowledge_hit_visible_for_retrieval({})
    assert knowledge_hit_visible_for_retrieval({"review_status": "approved"})
    assert knowledge_hit_visible_for_retrieval({"kind": "knowledge"})
    assert knowledge_hit_visible_for_retrieval(None)


def test_require_token_disabled(monkeypatch):
    from fastapi import HTTPException

    from app.kb_manual_admin import require_kb_manual_ingest_token  # type: ignore[import]

    monkeypatch.delenv("KB_MANUAL_INGEST_TOKEN", raising=False)
    with pytest.raises(HTTPException) as ei:
        require_kb_manual_ingest_token(None, None)
    assert ei.value.status_code == 404


def test_require_token_invalid(monkeypatch):
    from fastapi import HTTPException

    from app.kb_manual_admin import require_kb_manual_ingest_token  # type: ignore[import]

    monkeypatch.setenv("KB_MANUAL_INGEST_TOKEN", "secret")
    with pytest.raises(HTTPException) as ei:
        require_kb_manual_ingest_token("Bearer wrong", None)
    assert ei.value.status_code == 401


def test_require_token_header_ok(monkeypatch):
    from app.kb_manual_admin import require_kb_manual_ingest_token  # type: ignore[import]

    monkeypatch.setenv("KB_MANUAL_INGEST_TOKEN", "good")
    require_kb_manual_ingest_token(None, "good")


@pytest.mark.asyncio
async def test_run_approve_not_found(monkeypatch):
    from fastapi import HTTPException

    from app.clients.kb_client import KBConfig, QdrantKBClient  # type: ignore[import]
    from app.kb_manual_admin import ManualKnowledgeApproveBody, run_manual_knowledge_approve  # type: ignore[import]

    cfg = KBConfig(
        enabled=True,
        qdrant_url="http://x",
        knowledge_collection="k",
        experience_collection="e",
        experience_legacy_collection=None,
        static_tier_split=True,
        knowledge_manual_collection="trustguard_kb_manual",
        knowledge_cve_collection="c",
        knowledge_blogs_collection="b",
        boost_manual=1.5,
        boost_cve=1.2,
        boost_blogs=0.8,
        knowledge_unified_prefetch=0,
        top_k=5,
        embedding_model="x",
        embed_base_url="http://x",
        embed_api_key="k",
        auto_create=False,
    )
    client = QdrantKBClient(cfg)

    async def _apr(**kwargs):
        return False, "trustguard_kb_manual"

    client.approve_manual_knowledge_review = _apr  # type: ignore[method-assign]

    import app.clients.kb_client as kbc  # type: ignore[import]

    monkeypatch.setattr(kbc, "get_kb_config", lambda: cfg)
    monkeypatch.setattr(kbc, "get_kb_client", lambda: client)
    with pytest.raises(HTTPException) as ei:
        await run_manual_knowledge_approve(ManualKnowledgeApproveBody(point_id="nope"))
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_run_ingest_invalid_phase(monkeypatch):
    from fastapi import HTTPException

    from app.kb_manual_admin import ManualKnowledgeIngestBody, run_manual_knowledge_ingest  # type: ignore[import]

    monkeypatch.setenv("KB_MANUAL_INGEST_TOKEN", "x")
    with pytest.raises(HTTPException) as ei:
        await run_manual_knowledge_ingest(
            ManualKnowledgeIngestBody(body="hi", phase="NOT_A_PHASE"),
        )
    assert ei.value.status_code == 400
