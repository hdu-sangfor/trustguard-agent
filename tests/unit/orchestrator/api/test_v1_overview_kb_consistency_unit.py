import asyncio
import importlib
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


def test_v1_overview_kb_summary_matches_health():
    old_enabled = os.environ.get("KB_ENABLED")
    old_key = os.environ.get("KB_EMBED_API_KEY")
    try:
        os.environ["KB_ENABLED"] = "true"
        os.environ["KB_EMBED_API_KEY"] = "consistency-secret"
        mod = _load_main_module()
        health_body = asyncio.run(mod.health())
        overview_body = asyncio.run(mod.get_v1_overview())
        health_kb = health_body.get("v1_kb")
        overview_kb = overview_body.get("v1_kb")
        assert isinstance(health_kb, dict)
        assert isinstance(overview_kb, dict)
        assert overview_kb == health_kb
        assert overview_body.get("v1_mq_lanes") == health_body.get("v1_mq_lanes")
        assert overview_kb.get("enabled") is True
        assert overview_kb.get("has_embed_api_key") is True
        assert "embed_api_key" not in overview_kb
    finally:
        if old_enabled is None:
            os.environ.pop("KB_ENABLED", None)
        else:
            os.environ["KB_ENABLED"] = old_enabled
        if old_key is None:
            os.environ.pop("KB_EMBED_API_KEY", None)
        else:
            os.environ["KB_EMBED_API_KEY"] = old_key
