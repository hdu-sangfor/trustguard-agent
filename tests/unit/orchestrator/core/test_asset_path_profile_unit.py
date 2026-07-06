"""资产路径轮廓与 crawler URL 豁免单测。"""
from __future__ import annotations

import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def test_compute_asset_path_profile_struts2_heavy():
    from app.core.asset_path_profile import compute_asset_path_profile

    base = "http://host.docker.internal:8080"
    urls = [f"{base}/p{i}.action" for i in range(20)]
    p = compute_asset_path_profile(urls)
    assert p["dynamic_total"] == 20
    assert p["stack_hint"] == "struts2_heavy"
    assert p["java_suffix_ratio"] >= 0.15


def test_maybe_override_framework_from_asset_profile():
    from app.core.asset_path_profile import maybe_override_framework_from_asset_profile

    ctx: dict = {
        "asset_path_profile": {"stack_hint": "struts2_heavy", "dynamic_total": 20, "java_suffix_ratio": 0.9},
        "framework_target": "tomcat",
    }
    assert maybe_override_framework_from_asset_profile(ctx) is True
    assert ctx["framework_target"] == "struts2"
    assert ctx.get("framework_path_override") is True


def test_is_crawler_confirmed_url():
    from app.core.memory_store import is_crawler_confirmed_url

    ctx = {"crawler_confirmed_url_set": ["http://x/a.action", "http://x/b"]}
    assert is_crawler_confirmed_url(ctx, "http://x/a.action") is True
    assert is_crawler_confirmed_url(ctx, "http://unknown/z") is False
