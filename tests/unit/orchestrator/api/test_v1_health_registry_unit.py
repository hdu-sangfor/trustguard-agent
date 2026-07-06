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


def test_health_contains_v1_agent_registry_summary():
    mod = _load_main_module()
    body = asyncio.run(mod.health())
    assert body.get("status") == "ok"
    assert "v1_agent_registry" in body
    reg = body["v1_agent_registry"]
    assert isinstance(reg, dict)
    assert "total" in reg and "enabled" in reg and "agent_ids" in reg
    assert "capability_scores" in reg and "scores_source" in reg


def test_health_v1_agent_registry_capability_scores_from_env():
    old_reg = os.environ.get("V1_AGENT_REGISTRY")
    old_scores = os.environ.get("V1_AGENT_SCORE_MAP")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-x:recon:http:enabled:container"
        os.environ["V1_AGENT_SCORE_MAP"] = "agent-x:0.42"
        mod = _load_main_module()
        body = asyncio.run(mod.health())
        reg = body["v1_agent_registry"]
        assert reg.get("scores_source") == "env"
        assert reg.get("capability_scores") == {"agent-x": 0.42}
    finally:
        if old_reg is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_reg
        if old_scores is None:
            os.environ.pop("V1_AGENT_SCORE_MAP", None)
        else:
            os.environ["V1_AGENT_SCORE_MAP"] = old_scores


def test_health_contains_v1_scheduling_summary():
    old = os.environ.get("V1_SCHEDULING_POLICY")
    old_pid = os.environ.get("V1_PLANITEM_DISPATCH_ENABLED")
    try:
        os.environ["V1_SCHEDULING_POLICY"] = "round_robin"
        os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        mod = _load_main_module()
        body = asyncio.run(mod.health())
        sched = body.get("v1_scheduling")
        assert isinstance(sched, dict)
        assert sched.get("mode") == "round_robin"
        assert sched.get("observe_endpoint_available") is True
        assert sched.get("role_filter_enabled") is False
        assert sched.get("plan_item_dispatch_enabled") is False
    finally:
        if old is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old
        if old_pid is None:
            os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        else:
            os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = old_pid


def test_health_contains_v1_kb_summary_without_secret_value():
    old_enabled = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    try:
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = "super-secret"
        mod = _load_main_module()
        body = asyncio.run(mod.health())
        kb = body.get("v1_kb")
        assert isinstance(kb, dict)
        assert kb.get("enabled") is True
        assert kb.get("observe_endpoint_available") is True
        assert kb.get("has_embed_api_key") is True
        assert "embed_api_key" not in kb
    finally:
        if old_enabled is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_enabled
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key


def test_health_v1_kb_summary_openai_env_fallback_hides_secret():
    """仅 OPENAI_API_KEY 提供嵌入密钥时，`/health` 的 v1_kb 摘要仍不得序列化泄露明文。"""
    old_enabled = os.environ.get("KB_ENABLED")
    old_kb_key = os.environ.get("KB_EMBED_API_KEY")
    old_oai = os.environ.get("OPENAI_API_KEY")
    secret = "health-v1-kb-openai-fallback-secret-c4a91f"
    try:
        os.environ["KB_ENABLED"] = "true"
        os.environ.pop("KB_EMBED_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = secret

        mod = _load_main_module()
        body = asyncio.run(mod.health())
        dumped = json.dumps(body, ensure_ascii=False)

        assert secret not in dumped
        kb = body.get("v1_kb")
        assert isinstance(kb, dict)
        assert kb.get("enabled") is True
        assert kb.get("observe_endpoint_available") is True
        assert kb.get("has_embed_api_key") is True
        assert "embed_api_key" not in kb
        assert "openai_api_key" not in kb
    finally:
        if old_enabled is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_enabled
        if old_kb_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_kb_key
        if old_oai is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_oai


def test_health_contains_v1_mq_lanes_summary_defaults():
    """O-05 只读摘要：MQ 模式下单队列，`mq_topic_*` 同源；routing 与 mq_dispatch_ready 一致。"""
    old_tex = os.environ.get("MQ_TOPIC_EXECUTE")
    old_tag = os.environ.get("MQ_TOPIC_AGENT")
    old_mode = os.environ.get("EXECUTION_DISPATCH_MODE")
    old_broker = os.environ.get("MQ_BROKER_URL")
    try:
        os.environ.pop("MQ_TOPIC_EXECUTE", None)
        os.environ["MQ_TOPIC_AGENT"] = "q-agent"
        os.environ["EXECUTION_DISPATCH_MODE"] = "MQ"
        os.environ["MQ_BROKER_URL"] = "amqp://guest:guest@localhost:5672/"
        mod = _load_main_module()
        body = asyncio.run(mod.health())
        lanes = body.get("v1_mq_lanes")
        assert isinstance(lanes, dict)
        assert lanes.get("execution_dispatch_mode") == "mq"
        assert lanes.get("mq_broker_configured") is True
        assert lanes.get("mq_dispatch_ready") is True
        assert lanes.get("agent_lane_mq_enabled") is True
        assert lanes.get("mq_topic_execute") == "q-agent"
        assert lanes.get("mq_topic_agent") == "q-agent"
        assert lanes.get("agent_lane_allowlist_count") == 0
        assert lanes.get("agent_lane_routing_active") is True
        assert lanes.get("agent_lane_publish_ready") is True
    finally:
        if old_tex is None:
            os.environ.pop("MQ_TOPIC_EXECUTE", None)
        else:
            os.environ["MQ_TOPIC_EXECUTE"] = old_tex
        if old_tag is None:
            os.environ.pop("MQ_TOPIC_AGENT", None)
        else:
            os.environ["MQ_TOPIC_AGENT"] = old_tag
        if old_mode is None:
            os.environ.pop("EXECUTION_DISPATCH_MODE", None)
        else:
            os.environ["EXECUTION_DISPATCH_MODE"] = old_mode
        if old_broker is None:
            os.environ.pop("MQ_BROKER_URL", None)
        else:
            os.environ["MQ_BROKER_URL"] = old_broker


def test_health_v1_mq_lanes_invalid_dispatch_mode_falls_back_http():
    old_mode = os.environ.get("EXECUTION_DISPATCH_MODE")
    old_broker = os.environ.get("MQ_BROKER_URL")
    try:
        os.environ["EXECUTION_DISPATCH_MODE"] = "bogus"
        os.environ["MQ_BROKER_URL"] = "amqp://guest:guest@localhost:5672/"
        mod = _load_main_module()
        body = asyncio.run(mod.health())
        lanes = body.get("v1_mq_lanes", {})
        assert lanes.get("execution_dispatch_mode") == "http"
        assert lanes.get("mq_broker_configured") is True
        assert lanes.get("mq_dispatch_ready") is False
        assert lanes.get("agent_lane_publish_ready") is False
    finally:
        if old_mode is None:
            os.environ.pop("EXECUTION_DISPATCH_MODE", None)
        else:
            os.environ["EXECUTION_DISPATCH_MODE"] = old_mode
        if old_broker is None:
            os.environ.pop("MQ_BROKER_URL", None)
        else:
            os.environ["MQ_BROKER_URL"] = old_broker


def test_health_v1_mq_lanes_routing_active_when_mq_mode():
    old_mode = os.environ.get("EXECUTION_DISPATCH_MODE")
    old_broker = os.environ.get("MQ_BROKER_URL")
    try:
        os.environ["EXECUTION_DISPATCH_MODE"] = "mq"
        os.environ["MQ_BROKER_URL"] = "amqp://guest:guest@127.0.0.1:5672/"
        mod = _load_main_module()
        body = asyncio.run(mod.health())
        lanes = body.get("v1_mq_lanes")
        assert lanes.get("agent_lane_mq_enabled") is True
        assert lanes.get("agent_lane_allowlist_count") == 0
        assert lanes.get("agent_lane_routing_active") is True
        assert lanes.get("mq_dispatch_ready") is True
        assert lanes.get("agent_lane_publish_ready") is True
    finally:
        if old_mode is None:
            os.environ.pop("EXECUTION_DISPATCH_MODE", None)
        else:
            os.environ["EXECUTION_DISPATCH_MODE"] = old_mode
        if old_broker is None:
            os.environ.pop("MQ_BROKER_URL", None)
        else:
            os.environ["MQ_BROKER_URL"] = old_broker
