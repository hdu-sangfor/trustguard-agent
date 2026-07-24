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


def _allow_authenticated_user(monkeypatch, gateway, role: str = "ADMIN"):
    monkeypatch.setattr(
        gateway,
        "_require_user",
        lambda _authorization, _allowed_roles=None: {
            "username": "tester",
            "user_id": "user-test",
            "role": role,
            "status": "ACTIVE",
        },
    )


def test_knowledge_search_forwards_bounded_read_only_payload(monkeypatch):
    gateway = _load_gateway_main()
    _allow_authenticated_user(monkeypatch, gateway)
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

    response = asyncio.run(gateway.knowledge_search(request, authorization="Bearer test"))

    assert response["code"] == "0"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/search")
    assert kwargs["json_body"]["knowledge_base_id"] == "kb-security"
    assert kwargs["json_body"]["top_k"] == 6
    assert kwargs["json_body"]["enable_abstention"] is True
    assert kwargs["json_body"]["require_exact_entity_match"] is True


def test_knowledge_documents_keeps_selected_knowledge_base_scope(monkeypatch):
    gateway = _load_gateway_main()
    _allow_authenticated_user(monkeypatch, gateway)
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
            authorization="Bearer test",
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
    _allow_authenticated_user(monkeypatch, gateway)

    async def fake_rag(_method, _path, **_kwargs):
        return {"id": "doc-1", "knowledge_base_id": "kb-other"}

    monkeypatch.setattr(gateway, "_rag", fake_rag)

    try:
        asyncio.run(
            gateway.knowledge_document(
                document_id="doc-1",
                knowledge_base_id="kb-security",
                authorization="Bearer test",
            )
        )
    except gateway.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-base document must be rejected")


def test_knowledge_answer_forwards_grounded_generation_options(monkeypatch):
    gateway = _load_gateway_main()
    _allow_authenticated_user(monkeypatch, gateway)
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

    response = asyncio.run(gateway.knowledge_answer(request, authorization="Bearer test"))

    assert response["code"] == "0"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/answer")
    assert kwargs["timeout"] == 90.0
    assert kwargs["json_body"]["enable_query_rewrite"] is True
    assert kwargs["json_body"]["enable_abstention"] is True
    assert kwargs["json_body"]["require_exact_entity_match"] is True


def test_knowledge_base_create_forwards_management_payload(monkeypatch):
    gateway = _load_gateway_main()
    _allow_authenticated_user(monkeypatch, gateway)
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {
            "id": "kb-new",
            "name": "安全知识",
            "embedding_profile": "configured",
        }

    monkeypatch.setattr(gateway, "_rag", fake_rag)
    monkeypatch.setattr(gateway, "_record_audit", lambda *_args, **_kwargs: None)

    response = asyncio.run(
        gateway.create_knowledge_base(
            gateway.KnowledgeBaseCreateRequest(
                name="安全知识",
                description="单租户共享知识库",
            ),
            authorization="Bearer test",
        )
    )

    assert response["data"]["id"] == "kb-new"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/knowledge-bases")
    assert kwargs["json_body"] == {
        "name": "安全知识",
        "description": "单租户共享知识库",
        "embedding_profile": "configured",
    }


def test_upload_document_wraps_raw_file_as_rag_multipart(monkeypatch):
    gateway = _load_gateway_main()
    _allow_authenticated_user(monkeypatch, gateway)
    captured = []

    class FakeRequest:
        def __init__(self):
            self.headers = {
                "content-length": "11",
                "content-type": "text/plain",
            }

        async def body(self):
            return b"hello world"

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        if method == "GET":
            return {"id": "kb-security"}
        return {
            "job_id": "job-1",
            "status": "queued",
            "knowledge_base_id": "kb-security",
        }

    monkeypatch.setattr(gateway, "_rag", fake_rag)
    monkeypatch.setattr(gateway, "_record_audit", lambda *_args, **_kwargs: None)

    response = asyncio.run(
        gateway.upload_knowledge_document(
            "kb-security",
            FakeRequest(),
            filename="notes.txt",
            authorization="Bearer test",
        )
    )

    assert response["data"]["job_id"] == "job-1"
    method, path, kwargs = captured[1]
    assert (method, path) == ("POST", "/v1/ingest/jobs")
    assert kwargs["data"]["knowledge_base_id"] == "kb-security"
    assert kwargs["files"]["file"] == ("notes.txt", b"hello world", "text/plain")


def test_signed_auth_token_rejects_tampering_and_enforces_role(monkeypatch):
    gateway = _load_gateway_main()
    viewer = {
        "username": "viewer",
        "user_id": "user-viewer",
        "role": "VIEWER",
        "status": "ACTIVE",
    }
    monkeypatch.setattr(gateway, "_get_user_by_username", lambda username: viewer if username == "viewer" else None)

    token = gateway._token(viewer)
    assert gateway._require_user(
        f"Bearer {token}",
        gateway.KNOWLEDGE_READ_ROLES,
    )["username"] == "viewer"

    try:
        gateway._require_user(f"Bearer {token}x", gateway.KNOWLEDGE_READ_ROLES)
    except gateway.HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("tampered token must be rejected")

    try:
        gateway._require_user(f"Bearer {token}", gateway.KNOWLEDGE_WRITE_ROLES)
    except gateway.HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("viewer must not receive knowledge management permission")
