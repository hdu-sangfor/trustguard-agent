"""kb-r2c：NVD 解析、Blog HTML 净化、管道鉴权。"""
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.kb_blog_purify import strip_html_boilerplate  # type: ignore[import]
from app.kb_pipeline_nvd import parse_nvd_cve_response  # type: ignore[import]


def test_strip_html_boilerplate_basic():
    html = "<html><head><style>x{color:red}</style></head><body><p>Hello <b>world</b></p><script>evil()</script></body></html>"
    t = strip_html_boilerplate(html)
    assert "<" not in t
    assert "Hello" in t
    assert "world" in t
    assert "evil" not in t


def test_parse_nvd_cve_response_minimal():
    doc = {
        "vulnerabilities": [
            {
                "cve": {
                    "id": "CVE-2099-1",
                    "published": "2024-01-02T00:00:00.000",
                    "descriptions": [{"lang": "en", "value": "Test description."}],
                    "metrics": {
                        "cvssMetricV31": [
                            {
                                "cvssData": {"baseScore": 7.5, "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"}
                            }
                        ]
                    },
                }
            }
        ]
    }
    rows = parse_nvd_cve_response(doc)
    assert len(rows) == 1
    assert rows[0]["cve_id"] == "CVE-2099-1"
    assert "Test description" in rows[0]["summary"]
    assert rows[0]["snapshot"]["cvss_base_score"] == 7.5


def test_kb_pipeline_admin_token_disabled(monkeypatch):
    from fastapi import HTTPException

    from app.kb_pipeline_admin import require_kb_pipeline_admin_token  # type: ignore[import]

    monkeypatch.delenv("KB_PIPELINE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("KB_MANUAL_INGEST_TOKEN", raising=False)
    with pytest.raises(HTTPException) as ei:
        require_kb_pipeline_admin_token(None, None)
    assert ei.value.status_code == 404


def test_tiered_knowledge_collection_split():
    from app.clients.kb_client import KBConfig, tiered_knowledge_collection  # type: ignore[import]

    cfg = KBConfig(
        enabled=True,
        qdrant_url="http://q",
        knowledge_collection="uni",
        experience_collection="e",
        experience_legacy_collection=None,
        static_tier_split=True,
        knowledge_manual_collection="m",
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
    assert tiered_knowledge_collection(cfg, "cve") == "c"
    assert tiered_knowledge_collection(cfg, "blogs") == "b"
    assert tiered_knowledge_collection(cfg, "manual") == "m"


def test_tiered_knowledge_collection_unified():
    from app.clients.kb_client import KBConfig, tiered_knowledge_collection  # type: ignore[import]

    cfg = KBConfig(
        enabled=True,
        qdrant_url="http://q",
        knowledge_collection="uni",
        experience_collection="e",
        experience_legacy_collection=None,
        static_tier_split=False,
        knowledge_manual_collection="m",
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
    assert tiered_knowledge_collection(cfg, "cve") == "uni"
    assert tiered_knowledge_collection(cfg, "blogs") == "uni"
