import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT
from unittest.mock import patch


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


def test_v1_overview_aggregates_registry_and_scheduling():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_scores = os.environ.get("V1_AGENT_SCORE_MAP")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    try:
        os.environ["V1_AGENT_REGISTRY"] = (
            "agent-recon:recon:recon|http:enabled:container,"
            "agent-exploit:exploit:exploit|exploit:enabled:container"
        )
        os.environ["V1_AGENT_SCORE_MAP"] = "agent-recon:0.61,agent-exploit:0.88"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = "secret-not-in-body"
        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_overview(
                phase="EXPLOIT",
                task_id="task-overview",
                preferred_capability=None,
            )
        )
        assert "v1_agent_registry" in body
        assert "v1_scheduling" in body
        assert "v1_mq_lanes" in body
        assert "v1_kb" in body
        reg = body["v1_agent_registry"]
        sch = body["v1_scheduling"]
        mq = body["v1_mq_lanes"]
        kb = body["v1_kb"]
        assert reg.get("enabled") == 2
        assert reg.get("scores_source") == "env"
        assert reg.get("capability_scores") == {"agent-recon": 0.61, "agent-exploit": 0.88}
        assert sch.get("mode") == "capability_first"
        assert sch.get("selected_candidates") == ["agent-exploit"]
        assert mq.get("agent_lane_mq_enabled") is False
        assert mq.get("execution_dispatch_mode") == "http"
        assert mq.get("mq_broker_configured") is False
        assert mq.get("mq_dispatch_ready") is False
        assert mq.get("agent_lane_publish_ready") is False
        assert "mq_topic_execute" in mq and "mq_topic_agent" in mq
        assert kb.get("enabled") is True
        assert kb.get("observe_endpoint_available") is True
        assert kb.get("has_embed_api_key") is True
        assert "embed_api_key" not in kb
        assert sch.get("plan_item_dispatch_enabled") is False
        assert "plan_item" not in sch
        ti = body.get("v1_tactical_incremental")
        assert isinstance(ti, dict)
        assert ti.get("available") is False
        assert ti.get("items") == []
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_scores is None:
            os.environ.pop("V1_AGENT_SCORE_MAP", None)
        else:
            os.environ["V1_AGENT_SCORE_MAP"] = old_scores
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


def test_v1_overview_json_never_leaks_openai_env_fallback_secret():
    """仅 OPENAI_API_KEY 回退为嵌入密钥时，overview JSON 不得含明文且无敏感键名。"""
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    old_openai = os.environ.get("OPENAI_API_KEY")
    secret = "v1-overview-openai-fallback-secret-8f1e2b"
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "true"
        os.environ.pop("KB_EMBED_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = secret

        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_overview(
                phase="RECON",
                task_id="task-overview-oai-fallback",
                preferred_capability=None,
            )
        )
        dumped = json.dumps(body, ensure_ascii=False)
        assert secret not in dumped
        kb = body.get("v1_kb")
        assert isinstance(kb, dict)
        assert "embed_api_key" not in kb
        assert "openai_api_key" not in kb
        assert kb.get("has_embed_api_key") is True
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


def test_v1_overview_merges_planitem_from_scheduling_observe():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_kb = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    old_pid = os.environ.get("V1_PLANITEM_DISPATCH_ENABLED")
    old_pmeta = os.environ.get("V1_PLANITEM_METADATA_JSON")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["KB_ENABLED"] = "false"
        os.environ.pop("KB_EMBED_API_KEY", None)
        os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = "true"
        os.environ["V1_PLANITEM_METADATA_JSON"] = '{"plan_item_id":"ov-pi-2","plan_item_phase":"BYPASS"}'
        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_overview(
                phase="RECON",
                task_id="task-ov-pi",
                preferred_capability=None,
            )
        )
        sch = body["v1_scheduling"]
        assert sch.get("plan_item_dispatch_enabled") is True
        assert sch.get("plan_item") == {"plan_item_id": "ov-pi-2", "plan_item_phase": "BYPASS"}
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
        if old_pid is None:
            os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        else:
            os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = old_pid
        if old_pmeta is None:
            os.environ.pop("V1_PLANITEM_METADATA_JSON", None)
        else:
            os.environ["V1_PLANITEM_METADATA_JSON"] = old_pmeta


def test_v1_overview_includes_tactical_incremental_when_task_context_resolves():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        mod = _load_main_module()

        async def _fake_try(tid: str):
            if tid == "task-ti":
                return (
                    {"_tactical_incremental_artifacts": [{"kind": "finding", "summary": "hit"}]},
                    "memory",
                )
            return None

        with patch.object(mod, "_try_resolve_target_context", side_effect=_fake_try):
            body = asyncio.run(
                mod.get_v1_overview(phase="RECON", task_id="task-ti", preferred_capability=None)
            )
        blk = body.get("v1_tactical_incremental")
        assert isinstance(blk, dict)
        assert blk.get("available") is True
        assert blk.get("context_source") == "memory"
        assert isinstance(blk.get("items"), list) and len(blk["items"]) == 1
        assert blk["items"][0].get("kind") == "finding"
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
