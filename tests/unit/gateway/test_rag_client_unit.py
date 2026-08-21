import asyncio
import importlib
import sys

import httpx

from tests.paths import REPO_ROOT


def _load_rag_client_module():
    gateway_root = str(REPO_ROOT / "gateway")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, gateway_root)
    try:
        return importlib.import_module("app.clients.rag_client")
    finally:
        if gateway_root in sys.path:
            sys.path.remove(gateway_root)


def test_rag_client_injects_gateway_service_identity(monkeypatch):
    module = _load_rag_client_module()
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, kwargs=kwargs)
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    client = module.RagClient(
        base_url="http://rag.test",
        service_token="gateway-service-secret",
    )

    result = asyncio.run(client.request("GET", "/v1/knowledge-bases"))

    assert result == {"ok": True}
    assert captured["method"] == "GET"
    assert captured["url"] == "http://rag.test/v1/knowledge-bases"
    assert captured["kwargs"]["headers"] == {
        "Authorization": "Bearer gateway-service-secret"
    }


def test_rag_client_does_not_invent_service_identity(monkeypatch):
    module = _load_rag_client_module()
    monkeypatch.delenv("RAG_GATEWAY_SERVICE_TOKEN", raising=False)
    client = module.RagClient(base_url="http://rag.test")

    assert client._service_headers() is None


def test_rag_client_preserves_service_identity_when_forwarding_idempotency_key():
    module = _load_rag_client_module()
    client = module.RagClient(
        base_url="http://rag.test",
        service_token="gateway-service-secret",
    )

    assert client._service_headers(
        {
            "Idempotency-Key": "event-1",
            "Authorization": "Bearer browser-token-must-not-pass-through",
        }
    ) == {
        "Idempotency-Key": "event-1",
        "Authorization": "Bearer gateway-service-secret",
    }
