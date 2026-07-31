import importlib
import sys

import pytest
from starlette.requests import Request

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


@pytest.mark.asyncio
async def test_task_knowledge_chunks_returns_bounded_rag_previews(monkeypatch):
    gateway = _load_gateway_main()
    captured = []
    chunk_id = "chk-" + "a" * 32
    missing_id = "chk-" + "b" * 32

    monkeypatch.setattr(gateway, "_get_task_row", lambda _task_id: {"task_id": "task-kb"})

    async def fake_orch(method, path, **kwargs):
        captured.append((method, path, kwargs))
        return {
            "chunks": {
                chunk_id: {
                    "meta": {"chunk_type": "rag_knowledge"},
                    "content": {
                        "title": "ThinkPHP 专项验证",
                        "filename": "thinkphp.md",
                        "page_no": 3,
                        "text": "x" * 6001,
                        "source_ref": {
                            "source_type": "document",
                            "source_uri": "upload://fixture",
                            "scope": "penetration",
                        },
                        "metadata": {"content_type": "framework_routing"},
                    },
                },
                missing_id: None,
            }
        }

    monkeypatch.setattr(gateway, "_orch", fake_orch)
    request = Request({"type": "http", "headers": []})
    result = await gateway.task_knowledge_chunks(
        "task-kb",
        gateway.TaskKnowledgeChunksRequest(
            chunk_ids=[chunk_id, missing_id, "invalid", chunk_id]
        ),
        request,
    )

    assert result["code"] == "0"
    assert captured == [
        (
            "POST",
            "/v1/orchestrator/tasks/task-kb/chunks:batchGet",
            {
                "json_body": {"chunk_ids": [chunk_id, missing_id]},
                "headers": None,
                "timeout": 10.0,
            },
        )
    ]
    payload = result["data"]
    assert payload["missingChunkIds"] == [missing_id]
    assert payload["chunks"] == [
        {
            "chunkId": chunk_id,
            "title": "ThinkPHP 专项验证",
            "filename": "thinkphp.md",
            "pageNo": 3,
            "preview": "x" * 6000,
            "textLength": 6001,
            "truncated": True,
            "sourceType": "document",
            "sourceUri": "upload://fixture",
            "scope": "penetration",
            "contentType": "framework_routing",
        }
    ]
