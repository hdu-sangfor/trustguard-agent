"""context_pipeline：pipeline_* 标准键合并。"""

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest


@pytest.fixture
def cp_mod():
    root = str(REPO_ROOT / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, root)
    import importlib

    mod = importlib.import_module("app.core.context_pipeline")
    yield mod
    sys.path.remove(root)
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_normalize_pipeline_merges_keys(cp_mod):
    ctx = {
        "dispatcher_suspicious_signals": [
            {"url": "http://t/a.action", "reason": "path_keyword", "confidence": 0.4},
        ],
        "dispatcher_high_value_endpoints": ["http://t/upload"],
        "nuclei_tech_stack_evidence": [{"signal": "struts", "template_id": "x", "url": "http://t/", "severity": "info"}],
    }
    cp_mod.normalize_pipeline_context(ctx)
    assert isinstance(ctx.get("pipeline_signal_urls"), list)
    assert "http://t/a.action" in ctx["pipeline_signal_urls"]
    assert "http://t/upload" in ctx["pipeline_signal_urls"]
    assert len(ctx["pipeline_tech_stack_evidence"]) >= 1
    assert len(ctx["pipeline_suspicious_signals"]) >= 1
