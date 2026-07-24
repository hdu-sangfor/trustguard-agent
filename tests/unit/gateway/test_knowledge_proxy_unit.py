import asyncio
import importlib
import sys

from tests.paths import REPO_ROOT


def _load_gateway_main():
    gateway_root = str(REPO_ROOT / "gateway")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, gateway_root)
    try:
        return importlib.import_module("app.main")
    finally:
        if gateway_root in sys.path:
            sys.path.remove(gateway_root)


def test_knowledge_search_forwards_bounded_read_only_payload(monkeypatch):
    gateway = _load_gateway_main()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {"schema_version": "trustguard-search-v1", "results": []}

    monkeypatch.setattr(gateway, "_rag", fake_rag)
    request = gateway.RagSearchRequest(
        query="Apache Shiro RememberMe",
        knowledge_base_id="kb-security",
        top_k=6,
        retrieval_mode="focused",
    )

    response = asyncio.run(gateway.knowledge_search(request))

    assert response["code"] == "0"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/search")
    assert kwargs["json_body"]["knowledge_base_id"] == "kb-security"
    assert kwargs["json_body"]["top_k"] == 6
    assert kwargs["json_body"]["enable_abstention"] is True
    assert kwargs["json_body"]["require_exact_entity_match"] is True


def test_knowledge_documents_keeps_selected_knowledge_base_scope(monkeypatch):
    gateway = _load_gateway_main()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {"items": [], "total": 0, "offset": 0, "limit": 20}

    monkeypatch.setattr(gateway, "_rag", fake_rag)

    response = asyncio.run(
        gateway.knowledge_documents(
            knowledge_base_id="kb-security",
            offset=0,
            limit=20,
            status="ready",
            query="shiro",
        )
    )

    assert response["code"] == "0"
    method, path, kwargs = captured[0]
    assert (method, path) == ("GET", "/v1/documents")
    assert kwargs["params"] == {
        "knowledge_base_id": "kb-security",
        "offset": 0,
        "limit": 20,
        "status": "ready",
        "q": "shiro",
    }


def test_knowledge_document_rejects_cross_base_result(monkeypatch):
    gateway = _load_gateway_main()

    async def fake_rag(_method, _path, **_kwargs):
        return {"id": "doc-1", "knowledge_base_id": "kb-other"}

    monkeypatch.setattr(gateway, "_rag", fake_rag)

    try:
        asyncio.run(
            gateway.knowledge_document(
                document_id="doc-1",
                knowledge_base_id="kb-security",
            )
        )
    except gateway.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-base document must be rejected")


def test_knowledge_answer_forwards_grounded_generation_options(monkeypatch):
    gateway = _load_gateway_main()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {
            "status": "answered",
            "answer": "应验证反序列化风险。",
            "citations": [],
        }

    monkeypatch.setattr(gateway, "_rag", fake_rag)
    request = gateway.RagAnswerRequest(
        query="Apache Shiro RememberMe 有哪些风险？",
        knowledge_base_id="kb-security",
        top_k=5,
        retrieval_mode="comprehensive",
        enable_query_rewrite=True,
    )

    response = asyncio.run(gateway.knowledge_answer(request))

    assert response["code"] == "0"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/answer")
    assert kwargs["timeout"] == 90.0
    assert kwargs["json_body"]["enable_query_rewrite"] is True
    assert kwargs["json_body"]["enable_abstention"] is True
    assert kwargs["json_body"]["require_exact_entity_match"] is True
