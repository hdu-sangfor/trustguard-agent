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


def test_v1_scheduling_observe_returns_mode_and_candidates():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_rf = os.environ.get("V1_SCHEDULING_ROLE_FILTER_ENABLED")
    old_pid = os.environ.get("V1_PLANITEM_DISPATCH_ENABLED")
    old_pmeta = os.environ.get("V1_PLANITEM_METADATA_JSON")
    try:
        os.environ["V1_AGENT_REGISTRY"] = (
            "agent-recon:recon:recon|http:enabled:container,"
            "agent-exploit:exploit:exploit|exploit:enabled:container"
        )
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_scheduling_observe(
                phase="EXPLOIT",
                task_id="task-observe",
                preferred_capability=None,
            )
        )
        assert body.get("mode") == "capability_first"
        assert body.get("role_filter_enabled") is False
        assert body.get("plan_item_dispatch_enabled") is False
        assert "plan_item" not in body
        assert "plan_item_dispatch_error" not in body
        assert body.get("enabled_candidates") == ["agent-exploit", "agent-recon"]
        assert body.get("selected_candidates") == ["agent-exploit"]
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_rf is None:
            os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        else:
            os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = old_rf
        if old_pid is None:
            os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        else:
            os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = old_pid
        if old_pmeta is None:
            os.environ.pop("V1_PLANITEM_METADATA_JSON", None)
        else:
            os.environ["V1_PLANITEM_METADATA_JSON"] = old_pmeta


def test_v1_scheduling_observe_role_filter_narrows_selected():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_rf = os.environ.get("V1_SCHEDULING_ROLE_FILTER_ENABLED")
    old_pid = os.environ.get("V1_PLANITEM_DISPATCH_ENABLED")
    old_pmeta = os.environ.get("V1_PLANITEM_METADATA_JSON")
    try:
        os.environ["V1_AGENT_REGISTRY"] = (
            "agent-recon:recon:exploit|recon:enabled:container,"
            "agent-exploit:exploit:exploit|exploit:enabled:container"
        )
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = "true"
        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_scheduling_observe(
                phase="EXPLOIT",
                task_id="task-rf",
                preferred_capability=None,
            )
        )
        assert body.get("role_filter_enabled") is True
        assert body.get("plan_item_dispatch_enabled") is False
        assert body.get("selected_candidates") == ["agent-exploit"]
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_rf is None:
            os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        else:
            os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = old_rf
        if old_pid is None:
            os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        else:
            os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = old_pid
        if old_pmeta is None:
            os.environ.pop("V1_PLANITEM_METADATA_JSON", None)
        else:
            os.environ["V1_PLANITEM_METADATA_JSON"] = old_pmeta


def test_v1_scheduling_observe_planitem_metadata_when_flag_on():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_rf = os.environ.get("V1_SCHEDULING_ROLE_FILTER_ENABLED")
    old_pid = os.environ.get("V1_PLANITEM_DISPATCH_ENABLED")
    old_pmeta = os.environ.get("V1_PLANITEM_METADATA_JSON")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = "true"
        os.environ["V1_PLANITEM_METADATA_JSON"] = json.dumps(
            {
                "plan_item_id": "pi-demo-1",
                "plan_item_phase": "EXPLOIT",
                "plan_item_parent_ref": "parent-ref-9",
            }
        )
        mod = _load_main_module()
        body = asyncio.run(
            mod.get_v1_scheduling_observe(
                phase="RECON",
                task_id="task-pi",
                preferred_capability=None,
            )
        )
        assert body.get("plan_item_dispatch_enabled") is True
        assert body.get("plan_item") == {
            "plan_item_id": "pi-demo-1",
            "plan_item_phase": "EXPLOIT",
            "plan_item_parent_ref": "parent-ref-9",
        }
        assert "plan_item_dispatch_error" not in body
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_rf is None:
            os.environ.pop("V1_SCHEDULING_ROLE_FILTER_ENABLED", None)
        else:
            os.environ["V1_SCHEDULING_ROLE_FILTER_ENABLED"] = old_rf
        if old_pid is None:
            os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        else:
            os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = old_pid
        if old_pmeta is None:
            os.environ.pop("V1_PLANITEM_METADATA_JSON", None)
        else:
            os.environ["V1_PLANITEM_METADATA_JSON"] = old_pmeta


def test_v1_scheduling_observe_planitem_parse_failed():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_pid = os.environ.get("V1_PLANITEM_DISPATCH_ENABLED")
    old_pmeta = os.environ.get("V1_PLANITEM_METADATA_JSON")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = "1"
        os.environ["V1_PLANITEM_METADATA_JSON"] = "not-json"
        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_scheduling_observe())
        assert body.get("plan_item_dispatch_enabled") is True
        assert body.get("plan_item_dispatch_error") == "parse_failed"
        assert "plan_item" not in body
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_pid is None:
            os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        else:
            os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = old_pid
        if old_pmeta is None:
            os.environ.pop("V1_PLANITEM_METADATA_JSON", None)
        else:
            os.environ["V1_PLANITEM_METADATA_JSON"] = old_pmeta


def test_v1_scheduling_observe_planitem_invalid_without_id():
    old_registry = os.environ.get("V1_AGENT_REGISTRY")
    old_mode = os.environ.get("V1_SCHEDULING_POLICY")
    old_pid = os.environ.get("V1_PLANITEM_DISPATCH_ENABLED")
    old_pmeta = os.environ.get("V1_PLANITEM_METADATA_JSON")
    try:
        os.environ["V1_AGENT_REGISTRY"] = "agent-a:recon:recon|http:enabled:container"
        os.environ["V1_SCHEDULING_POLICY"] = "capability_first"
        os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = "true"
        os.environ["V1_PLANITEM_METADATA_JSON"] = json.dumps({"plan_item_phase": "RECON"})
        mod = _load_main_module()
        body = asyncio.run(mod.get_v1_scheduling_observe())
        assert body.get("plan_item_dispatch_error") == "invalid_metadata"
        assert "plan_item" not in body
    finally:
        if old_registry is None:
            os.environ.pop("V1_AGENT_REGISTRY", None)
        else:
            os.environ["V1_AGENT_REGISTRY"] = old_registry
        if old_mode is None:
            os.environ.pop("V1_SCHEDULING_POLICY", None)
        else:
            os.environ["V1_SCHEDULING_POLICY"] = old_mode
        if old_pid is None:
            os.environ.pop("V1_PLANITEM_DISPATCH_ENABLED", None)
        else:
            os.environ["V1_PLANITEM_DISPATCH_ENABLED"] = old_pid
        if old_pmeta is None:
            os.environ.pop("V1_PLANITEM_METADATA_JSON", None)
        else:
            os.environ["V1_PLANITEM_METADATA_JSON"] = old_pmeta
