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


def test_v1_health_overview_returns_health_and_overview():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    try:
        os.environ["V1_AGENT_REGISTRY"] = (
            "agent-recon:recon:recon|http:enabled:container,"
            "agent-exploit:exploit:exploit|exploit:enabled:container"
        )
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = "health-overview-secret"
        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_health_overview(
                phase="EXPLOIT",
                task_id="task-health-overview",
                preferred_capability=None,
            )
        )
        assert "health" in body and "overview" in body
        assert body.get("request") == {
            "phase": "EXPLOIT",
            "task_id": "task-health-overview",
            "preferred_capability": None,
        }
        health_part = body["health"]
        overview_part = body["overview"]
        assert health_part.get("status") == "ok"
        assert "v1_kb" in health_part
        assert "v1_kb" in overview_part
        assert overview_part["v1_kb"] == health_part["v1_kb"]
        assert "v1_mq_lanes" in health_part
        assert "v1_mq_lanes" in overview_part
        assert overview_part["v1_mq_lanes"] == health_part["v1_mq_lanes"]
        assert overview_part["v1_scheduling"].get("selected_candidates") == ["agent-exploit"]
        assert overview_part["v1_scheduling"].get("plan_item_dispatch_enabled") is False
        assert health_part["v1_scheduling"].get("plan_item_dispatch_enabled") is False
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_kb is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_kb
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key


def test_v1_health_overview_json_never_contains_embed_secret_literal():
    """红线：health-overview 聚合响应序列化后不得出现嵌入密钥明文。"""
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    old_openai = os.environ.get("OPENAI_API_KEY")
    secret = "health-overview-redline-secret-7b2d9e41"
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = secret
        os.environ.pop("OPENAI_API_KEY", None)

        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_health_overview(
                phase="RECON",
                task_id="task-ho-redline",
                preferred_capability=None,
            )
        )
        dumped = json.dumps(body, ensure_ascii=False)
        assert secret not in dumped
        assert body.get("request", {}).get("task_id") == "task-ho-redline"
        health_kb = body.get("health", {}).get("v1_kb")
        overview_kb = body.get("overview", {}).get("v1_kb")
        assert isinstance(health_kb, dict)
        assert isinstance(overview_kb, dict)
        assert "embed_api_key" not in health_kb
        assert "embed_api_key" not in overview_kb
        assert health_kb.get("has_embed_api_key") is True
        assert overview_kb.get("has_embed_api_key") is True
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_kb is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_kb
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key
        if old_openai is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_openai


def test_v1_health_overview_json_never_leaks_openai_env_fallback_secret():
    """仅 OPENAI_API_KEY 作为嵌入密钥回退时，聚合 JSON 不得含明文且无敏感键名。"""
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    old_openai = os.environ.get("OPENAI_API_KEY")
    secret = "health-overview-openai-fallback-secret-91d3ac"
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "true"
        os.environ.pop("KB_EMBED_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = secret

        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_health_overview(
                phase="RECON",
                task_id="task-ho-oai-fallback",
                preferred_capability=None,
            )
        )
        dumped = json.dumps(body, ensure_ascii=False)
        assert secret not in dumped
        health_kb = body.get("health", {}).get("v1_kb")
        overview_kb = body.get("overview", {}).get("v1_kb")
        assert "embed_api_key" not in health_kb
        assert "openai_api_key" not in health_kb
        assert "embed_api_key" not in overview_kb
        assert "openai_api_key" not in overview_kb
        assert health_kb.get("has_embed_api_key") is True
        assert overview_kb.get("has_embed_api_key") is True
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_kb is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_kb
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key
        if old_openai is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_openai


def test_v1_health_overview_overview_matches_standalone_overview():
    """聚合端点内嵌 overview 与独立 overview 端点一致，防止实现分叉。"""
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    try:
        os.environ["V1_AGENT_REGISTRY"] = (
            "agent-recon:recon:recon|http:enabled:container,"
            "agent-exploit:exploit:exploit|exploit:enabled:container"
        )
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = "ho-consistency-key"

        mod = _load_main_module()
        phase = "RECON"
        task_id = "consistency-ho-task"
        preferred = "exploit"
        ho = asyncio.run(
            mod.get_v1_health_overview(
                phase=phase,
                task_id=task_id,
                preferred_capability=preferred,
            )
        )
        standalone = asyncio.run(
            mod.get_v1_overview(
                phase=phase,
                task_id=task_id,
                preferred_capability=preferred,
            )
        )
        assert ho.get("overview") == standalone
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_kb is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_kb
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key


def test_v1_health_overview_request_matches_overview_request():
    """聚合 request 回显与 overview.v1_scheduling.request 必须一致。"""
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "false"
        mod = _load_main_module()
        phase = "RECON"
        task_id = "req-match-task"
        preferred = "http"
        body = asyncio.run(
            mod.get_v1_health_overview(
                phase=phase,
                task_id=task_id,
                preferred_capability=preferred,
            )
        )
        req = body.get("request")
        overview_req = body.get("overview", {}).get("v1_scheduling", {}).get("request")
        assert req == {
            "phase": phase,
            "task_id": task_id,
            "preferred_capability": preferred,
        }
        assert overview_req == req
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_kb is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_kb


def test_v1_health_overview_health_matches_standalone_health():
    """聚合端点内嵌 health 与独立 /health 同源，防止实现分叉。"""
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "round_robin"
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = "ho-health-match-key"

        mod = _load_main_module()

        async def _run():
            body = await mod.get_v1_health_overview(
                phase="RECON",
                task_id="health-match-task",
                preferred_capability="http",
            )
            standalone = await mod.health()
            return body, standalone

        body, standalone = asyncio.run(_run())
        assert body.get("health") == standalone
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_kb is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_kb
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key


def test_v1_health_overview_kb_matches_standalone_kb_observe():
    """聚合端点内嵌 v1_kb 与独立 kb-observe 同源一致。"""
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_fed = os.environ.get("V1_KB_FEDERATION_OBSERVE_ENABLED")
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
        os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        os.environ.pop("V1_KB_FEDERATION_STORE_BACKEND", None)
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_QDRANT_URL"] = "http://kb-qdrant:6333"
        os.environ["KB_COLLECTION_KNOWLEDGE"] = "kb_knowledge_v1"
        os.environ["KB_COLLECTION_EXPERIENCE"] = "kb_experience_v1"
        os.environ["KB_TOP_K"] = "9"
        os.environ["KB_EMBED_MODEL"] = "text-embedding-v1"
        os.environ["KB_EMBED_BASE_URL"] = "https://embed.v1.local/v1"
        os.environ["KB_EMBED_API_KEY"] = "kb-match-secret"

        mod = _load_main_module()

        async def _run():
            body = await mod.get_v1_health_overview(
                phase="RECON",
                task_id="kb-match-task",
                preferred_capability="http",
            )
            kb = await mod.get_v1_kb_observe()
            return body, kb

        body, kb = asyncio.run(_run())
        expected_summary = {
            "enabled": kb.get("enabled"),
            "has_embed_api_key": kb.get("has_embed_api_key"),
            "observe_endpoint_available": True,
            "kb_federation_observe_enabled": False,
            "kb_federation_observe_endpoint_available": True,
            "kb_federation_store_enabled": False,
            "kb_federation_store_backend": "memory",
            "kb_federation_store_admin_available": True,
        }
        assert body.get("overview", {}).get("v1_kb") == expected_summary
        assert body.get("health", {}).get("v1_kb") == expected_summary
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_fed is None:
            os.environ.pop("V1_KB_FEDERATION_OBSERVE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_OBSERVE_ENABLED"] = old_fed
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
