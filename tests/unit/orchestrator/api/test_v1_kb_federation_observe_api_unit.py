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


def test_kb_federation_observe_disabled_minimal_payload():
    old = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
    try:
        os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_kb_federation_observe())
        assert body.get("kb_federation_observe_enabled") is False
        assert body.get("stub") is True
        assert "aggregate" not in body
    finally:
        if old is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old


def test_kb_federation_observe_enabled_includes_aggregate():
    old = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
    old_store = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
    try:
        os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = "true"
        os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_kb_federation_observe())
        assert body.get("kb_federation_observe_enabled") is True
        assert body.get("stub") is True
        agg = body.get("aggregate")
        assert isinstance(agg, dict)
        assert agg.get("tasks_tracked") == 0
        assert agg.get("by_phase") == {"RECON": 0, "EXPLOIT": 0, "BYPASS": 0}
    finally:
        if old is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old
        if old_store is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old_store


def test_kb_federation_observe_aggregate_reads_store_when_enabled():
    old_obs = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
    old_store = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
    old_back = os.environ.get("V1_KB_FEDERATION_STORE_BACKEND")
    try:
        os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = "true"
        os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = "true"
        os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = "memory"
        mod = _load_main_module()
        created = asyncio.run(
            mod.upsert_v1_kb_federation_meta_create(
                mod.KbFederationMetaUpsertBody(
                    task_id="task-fed-agg",
                    phase="RECON",
                    kb_entry_id="kb-entry-1",
                )
            )
        )
        eid = created["id"]
        body = asyncio.run(mod.get_v1_kb_federation_observe(sample_limit=5))
        assert body.get("stub") is False
        agg = body.get("aggregate")
        assert agg.get("tasks_tracked") == 1
        assert agg.get("entries_total") == 1
        assert agg.get("by_phase")["RECON"] == 1
        assert agg.get("sample_limit") == 5
        assert len(agg.get("sample_entry_ids") or []) <= 5
        assert eid in (agg.get("sample_entry_ids") or [])
    finally:
        if old_obs is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old_obs
        if old_store is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old_store
        if old_back is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_BACKEND", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = old_back


def test_kb_federation_observe_sample_limit_is_bounded():
    old_obs = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
    old_store = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
    old_back = os.environ.get("V1_KB_FEDERATION_STORE_BACKEND")
    try:
        os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = "true"
        os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = "true"
        os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = "memory"
        mod = _load_main_module()
        for i in range(12):
            asyncio.run(
                mod.upsert_v1_kb_federation_meta_create(
                    mod.KbFederationMetaUpsertBody(
                        task_id=f"task-fed-limit-{i}",
                        phase="RECON",
                        kb_entry_id=f"kb-{i}",
                    )
                )
            )
        body = asyncio.run(mod.get_v1_kb_federation_observe(sample_limit=999))
        agg = body.get("aggregate") or {}
        assert agg.get("sample_limit") == 50
        assert len(agg.get("sample_entry_ids") or []) <= 50
    finally:
        if old_obs is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old_obs
        if old_store is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old_store
        if old_back is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_BACKEND", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = old_back


def test_health_v1_kb_reflects_federation_flag():
    old_fed = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
    old_store = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    try:
        os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = "1"
        os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = "kb-federation-health-test-secret"
        mod = _load_main_module()
        body = asyncio.run(mod.health())
        kb = body.get("v1_kb")
        assert kb.get("kb_federation_observe_enabled") is True
        assert kb.get("kb_federation_observe_endpoint_available") is True
        assert kb.get("kb_federation_store_enabled") is False
        assert kb.get("kb_federation_store_backend") == "memory"
        assert kb.get("kb_federation_store_admin_available") is True
        dumped = json.dumps(body, ensure_ascii=False)
        assert "kb-federation-health-test-secret" not in dumped
    finally:
        if old_fed is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old_fed
        if old_store is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old_store
        if old_kb is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_kb
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key


def test_kb_federation_observe_json_never_contains_kb_embed_secret_literal():
    """联邦观测 PoC 响应序列化不得出现 KB_EMBED_API_KEY 明文。"""
    old_fed = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
    env_backup = {k: os.environ.get(k) for k in ["KB_ENABLED", "KB_EMBED_API_KEY", "OPENAI_API_KEY"]}
    secret = "kb-fed-observe-redline-embed-7c2a91"
    try:
        os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = "true"
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = secret
        os.environ.pop("OPENAI_API_KEY", None)
        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_kb_federation_observe())
        dumped = json.dumps(body, ensure_ascii=False)
        assert secret not in dumped
        assert "embed_api_key" not in dumped
        assert "openai_api_key" not in dumped
    finally:
        if old_fed is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old_fed
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_kb_federation_observe_json_never_leaks_openai_env_fallback_secret():
    """仅 OPENAI_API_KEY 作为嵌入回退时，联邦观测 PoC 序列化仍不得泄露明文。"""
    old_fed = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
    env_backup = {k: os.environ.get(k) for k in ["KB_ENABLED", "KB_EMBED_API_KEY", "OPENAI_API_KEY"]}
    secret = "kb-fed-observe-openai-fallback-9e1d4b"
    try:
        os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = "1"
        os.environ["KB_ENABLED"] = "true"
        os.environ.pop("KB_EMBED_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = secret
        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_kb_federation_observe())
        dumped = json.dumps(body, ensure_ascii=False)
        assert secret not in dumped
        assert "openai_api_key" not in dumped
    finally:
        if old_fed is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old_fed
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
