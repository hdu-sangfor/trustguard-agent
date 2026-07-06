import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_main_module():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.main")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_v1_kb_observe_returns_sanitized_kb_summary():
    env_backup = {k: os.environ.get(k) for k in [
        "KB_ENABLED",
        "KB_QDRANT_URL",
        "KB_COLLECTION_KNOWLEDGE",
        "KB_COLLECTION_EXPERIENCE",
        "KB_TOP_K",
        "KB_EMBED_MODEL",
        "KB_EMBED_BASE_URL",
        "KB_EMBED_API_KEY",
    ]}
    try:
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_QDRANT_URL"] = "http://kb-qdrant:6333"
        os.environ["KB_COLLECTION_KNOWLEDGE"] = "k_knowledge"
        os.environ["KB_COLLECTION_EXPERIENCE"] = "k_experience"
        os.environ["KB_TOP_K"] = "7"
        os.environ["KB_EMBED_MODEL"] = "text-embedding-test"
        os.environ["KB_EMBED_BASE_URL"] = "https://embed.local/v1"
        os.environ["KB_EMBED_API_KEY"] = "secret_should_not_be_returned"

        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_kb_observe())

        assert body.get("enabled") is True
        assert body.get("qdrant_url") == "http://kb-qdrant:6333"
        assert body.get("knowledge_collection") == "k_knowledge"
        assert body.get("experience_collection") == "k_experience"
        assert body.get("top_k") == 7
        assert body.get("embedding_model") == "text-embedding-test"
        assert body.get("embed_base_url") == "https://embed.local/v1"
        assert body.get("has_embed_api_key") is True
        assert "embed_api_key" not in body
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_v1_kb_observe_json_never_contains_embed_secret_literal():
    """红线：kb-observe 响应序列化后不得出现嵌入密钥明文。"""
    env_backup = {k: os.environ.get(k) for k in [
        "KB_ENABLED",
        "KB_EMBED_API_KEY",
        "OPENAI_API_KEY",
    ]}
    secret = "kb-observe-redline-secret-9f3a2c1e"
    try:
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = secret
        os.environ.pop("OPENAI_API_KEY", None)

        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_kb_observe())
        dumped = json.dumps(body, ensure_ascii=False)

        assert secret not in dumped
        assert "embed_api_key" not in body
        assert "openai_api_key" not in body
        assert body.get("has_embed_api_key") is True
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_v1_kb_observe_json_never_leaks_openai_env_fallback_secret():
    """
    向量密钥仅来自 OPENAI_API_KEY（未设 KB_EMBED_API_KEY）时，`get_kb_config` 会回退读取；
    kb-observe 响应序列化仍不得出现该明文，且不得出现敏感键名。
    """
    env_backup = {k: os.environ.get(k) for k in [
        "KB_ENABLED",
        "KB_EMBED_API_KEY",
        "OPENAI_API_KEY",
    ]}
    secret = "openai-fallback-redline-secret-b7e4d2a9"
    try:
        os.environ["KB_ENABLED"] = "true"
        os.environ.pop("KB_EMBED_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = secret

        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_kb_observe())
        dumped = json.dumps(body, ensure_ascii=False)

        assert secret not in dumped
        assert "embed_api_key" not in body
        assert "openai_api_key" not in body
        assert body.get("has_embed_api_key") is True
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
