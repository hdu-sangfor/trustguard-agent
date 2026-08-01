import asyncio
import importlib
import sys

from tests.paths import REPO_ROOT


def _load_gateway_modules():
    gateway_root = str(REPO_ROOT / "gateway")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, gateway_root)
    try:
        importlib.import_module("app.main")
        return (
            importlib.import_module("app.api.knowledge"),
            importlib.import_module("app.schemas.knowledge"),
            importlib.import_module("app.security.auth"),
        )
    finally:
        if gateway_root in sys.path:
            sys.path.remove(gateway_root)


def _user(auth, role: str = "ADMIN"):
    return auth.CurrentUser(
        user_id="user-test",
        username="tester",
        role=role,
        status="ACTIVE",
    )


def test_knowledge_search_forwards_bounded_read_only_payload(monkeypatch):
    knowledge, schemas, auth = _load_gateway_modules()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {"schema_version": "trustguard-search-v1", "results": []}

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    request = schemas.RagSearchRequest(
        query="Apache Shiro RememberMe",
        knowledge_base_id="kb-security",
        top_k=6,
        retrieval_mode="focused",
    )

    response = asyncio.run(
        knowledge.knowledge_search(request, _user(auth, "VIEWER"))
    )

    assert response["code"] == "0"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/search")
    assert kwargs["json_body"]["knowledge_base_id"] == "kb-security"
    assert kwargs["json_body"]["top_k"] == 6
    assert kwargs["json_body"]["enable_abstention"] is True
    assert kwargs["json_body"]["require_exact_entity_match"] is True


def test_knowledge_documents_keeps_selected_knowledge_base_scope(monkeypatch):
    knowledge, _schemas, auth = _load_gateway_modules()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {"items": [], "total": 0, "offset": 0, "limit": 20}

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)

    response = asyncio.run(
        knowledge.knowledge_documents(
            _user(auth, "VIEWER"),
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
    knowledge, _schemas, auth = _load_gateway_modules()

    async def fake_rag(_method, _path, **_kwargs):
        return {"id": "doc-1", "knowledge_base_id": "kb-other"}

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)

    try:
        asyncio.run(
            knowledge.knowledge_document(
                "doc-1",
                _user(auth, "VIEWER"),
                knowledge_base_id="kb-security",
            )
        )
    except knowledge.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-base document must be rejected")


def test_knowledge_answer_forwards_grounded_generation_options(monkeypatch):
    knowledge, schemas, auth = _load_gateway_modules()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {
            "status": "answered",
            "answer": "应验证反序列化风险。",
            "citations": [],
        }

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    request = schemas.RagAnswerRequest(
        query="Apache Shiro RememberMe 有哪些风险？",
        knowledge_base_id="kb-security",
        top_k=5,
        retrieval_mode="comprehensive",
        enable_query_rewrite=True,
    )

    response = asyncio.run(
        knowledge.knowledge_answer(request, _user(auth, "VIEWER"))
    )

    assert response["code"] == "0"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/answer")
    assert kwargs["timeout"] == 90.0
    assert kwargs["json_body"]["enable_query_rewrite"] is True
    assert kwargs["json_body"]["enable_abstention"] is True
    assert kwargs["json_body"]["require_exact_entity_match"] is True


def test_knowledge_base_create_forwards_management_payload(monkeypatch):
    knowledge, schemas, auth = _load_gateway_modules()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {
            "id": "kb-new",
            "name": "安全知识",
            "embedding_profile": "configured",
        }

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    monkeypatch.setattr(
        knowledge,
        "record_audit",
        lambda *_args, **_kwargs: None,
    )

    response = asyncio.run(
        knowledge.create_knowledge_base(
            schemas.KnowledgeBaseCreateRequest(
                name="安全知识",
                description="单租户共享知识库",
            ),
            _user(auth),
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
    knowledge, _schemas, auth = _load_gateway_modules()
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

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    monkeypatch.setattr(
        knowledge,
        "record_audit",
        lambda *_args, **_kwargs: None,
    )

    response = asyncio.run(
        knowledge.upload_knowledge_document(
            "kb-security",
            FakeRequest(),
            _user(auth),
            filename="notes.txt",
        )
    )

    assert response["data"]["job_id"] == "job-1"
    method, path, kwargs = captured[1]
    assert (method, path) == ("POST", "/v1/ingest/jobs")
    assert kwargs["data"]["knowledge_base_id"] == "kb-security"
    assert kwargs["files"]["file"] == (
        "notes.txt",
        b"hello world",
        "text/plain",
    )


def test_signed_auth_token_rejects_tampering_and_enforces_role(monkeypatch):
    _knowledge, _schemas, auth = _load_gateway_modules()
    viewer_row = {
        "username": "viewer",
        "user_id": "user-viewer",
        "role": "VIEWER",
        "status": "ACTIVE",
    }
    monkeypatch.setattr(
        auth,
        "get_user_by_username",
        lambda username: viewer_row if username == "viewer" else None,
    )

    token = auth.issue_token(viewer_row)
    viewer = auth.get_current_user(authorization=f"Bearer {token}")
    assert viewer.username == "viewer"

    try:
        auth.get_current_user(authorization=f"Bearer {token}x")
    except auth.HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("tampered token must be rejected")

    manager_dependency = auth.require_roles("ADMIN", "OPERATOR")
    try:
        manager_dependency(user=viewer)
    except auth.HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError(
            "viewer must not receive knowledge management permission"
        )


def test_knowledge_router_rejects_missing_login_header():
    _knowledge, _schemas, _auth = _load_gateway_modules()
    gateway = sys.modules["app.main"]
    from fastapi.testclient import TestClient

    response = TestClient(gateway.app).get("/api/v1/knowledge/bases")

    assert response.status_code == 401
    assert response.json()["message"] == "缺少、过期或无效的登录凭证"


def test_knowledge_router_dependency_rejects_viewer_write():
    _knowledge, _schemas, auth = _load_gateway_modules()
    gateway = sys.modules["app.main"]
    from fastapi.testclient import TestClient

    gateway.app.dependency_overrides[auth.get_current_user] = lambda: _user(
        auth,
        "VIEWER",
    )
    try:
        response = TestClient(gateway.app).post(
            "/api/v1/knowledge/bases",
            json={
                "name": "forbidden",
                "embedding_profile": "configured",
            },
        )
    finally:
        gateway.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["message"] == "当前角色没有执行此操作的权限"


def test_crawler_presets_only_expose_agent_categories(monkeypatch):
    knowledge, _schemas, auth = _load_gateway_modules()

    async def fake_rag(method, path, **_kwargs):
        assert (method, path) == ("GET", "/v1/crawler/presets")
        return {
            "items": [
                {"id": "agent_01_asset_fingerprint", "kind": "category"},
                {"id": "international_security_news", "kind": "source"},
            ]
        }

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    response = asyncio.run(
        knowledge.knowledge_crawler_presets(_user(auth, "VIEWER"))
    )

    assert response["data"]["items"] == [
        {"id": "agent_01_asset_fingerprint", "kind": "category"}
    ]


def test_create_crawler_job_forwards_bounded_agent_payload(monkeypatch):
    knowledge, schemas, auth = _load_gateway_modules()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {
            "id": "crawl-1",
            "knowledge_base_id": "kb-assets",
            "status": "queued",
        }

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    monkeypatch.setattr(knowledge, "record_audit", lambda *_args: None)
    request = schemas.KnowledgeCrawlerCreateRequest(
        knowledge_base_id="kb-assets",
        preset_ids=["agent_01_asset_fingerprint"],
        keywords=["CPE fingerprint"],
        max_total_pages=60,
    )

    response = asyncio.run(
        knowledge.create_knowledge_crawler_job(request, _user(auth))
    )

    assert response["data"]["id"] == "crawl-1"
    method, path, kwargs = captured[0]
    assert (method, path) == ("POST", "/v1/crawler/jobs")
    assert kwargs["json_body"]["preset_ids"] == [
        "agent_01_asset_fingerprint"
    ]
    assert kwargs["json_body"]["max_total_pages"] == 60
    assert kwargs["json_body"]["require_review"] is True
    assert kwargs["json_body"]["review_mode"] == "human"
    assert "structured_sources" not in kwargs["json_body"]


def test_create_crawler_job_forwards_agent_review_criteria(monkeypatch):
    knowledge, schemas, auth = _load_gateway_modules()
    captured = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {"id": "crawl-agent", "knowledge_base_id": "kb-vuln", "status": "queued"}

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    monkeypatch.setattr(knowledge, "record_audit", lambda *_args: None)
    request = schemas.KnowledgeCrawlerCreateRequest(
        knowledge_base_id="kb-vuln",
        preset_ids=["agent_02_vulnerability_weakness"],
        review_mode="agent",
        review_criteria="必须包含漏洞标识、影响范围和修复信息",
    )

    asyncio.run(knowledge.create_knowledge_crawler_job(request, _user(auth)))

    payload = captured[0][2]["json_body"]
    assert payload["require_review"] is True
    assert payload["review_mode"] == "agent"
    assert payload["review_criteria"] == "必须包含漏洞标识、影响范围和修复信息"


def test_crawler_job_control_rejects_cross_base_job(monkeypatch):
    knowledge, _schemas, auth = _load_gateway_modules()
    calls = []

    async def fake_rag(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "crawl-1", "knowledge_base_id": "kb-other"}

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)

    try:
        asyncio.run(
            knowledge.pause_knowledge_crawler_job(
                "crawl-1",
                _user(auth),
                knowledge_base_id="kb-assets",
            )
        )
    except knowledge.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-base crawler control must be rejected")

    assert len(calls) == 1


def test_crawler_review_approval_is_scoped_and_audited(monkeypatch):
    knowledge, schemas, auth = _load_gateway_modules()
    captured = []
    audits = []

    async def fake_rag(method, path, **kwargs):
        captured.append((method, path, kwargs))
        if method == "GET":
            return {"id": "crawl-1", "knowledge_base_id": "kb-assets"}
        return {
            "job_id": "crawl-1",
            "review_status": "completed",
            "items": [],
            "pending": 0,
            "approved": 2,
            "rejected": 0,
        }

    monkeypatch.setattr(knowledge.rag_client, "request", fake_rag)
    monkeypatch.setattr(
        knowledge,
        "record_audit",
        lambda *args: audits.append(args),
    )
    response = asyncio.run(
        knowledge.review_knowledge_crawler_job(
            "crawl-1",
            schemas.KnowledgeCrawlerReviewRequest(
                action="approve",
                item_ids=["review-1", "review-2"],
            ),
            _user(auth),
            knowledge_base_id="kb-assets",
        )
    )

    assert response["data"]["approved"] == 2
    assert captured[1][0:2] == (
        "POST",
        "/v1/crawler/jobs/crawl-1/review",
    )
    assert captured[1][2]["json_body"] == {
        "action": "approve",
        "item_ids": ["review-1", "review-2"],
    }
    assert audits[0][0] == "KNOWLEDGE_CRAWLER_REVIEW_APPROVE"
