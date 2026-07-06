"""
单元测试：web-recon-v1 kit 扩展 ehole / read_workspace_artifact 成员。

实地证据 (task-c90db15cf652498eab05c5033612f1d1, thinkphp 5.0.23):
- LLM 在 RECON 阶段给 PlanList item 声明 kit_id=web-recon-v1
- 但 skill_id=ehole / skill_id=read_workspace_artifact 被判 skill_not_in_capability_kit
- 5 次 PLAN_LIST_BUSINESS_REJECT，任务死循环

修复：把 ehole（指纹识别）和 read_workspace_artifact（通用复盘）加入 kit 成员。
"""
import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def test_web_recon_v1_includes_ehole_and_artifact_reader():
    from app.core.capability_kits import (
        get_kit_member_tools,
        reload_kit_registry_for_tests,
    )
    reload_kit_registry_for_tests()
    members = get_kit_member_tools("web-recon-v1")
    assert members is not None
    assert "ehole" in members, f"ehole should be in web-recon-v1, got {members}"
    assert "read_workspace_artifact" in members, f"read_workspace_artifact should be in web-recon-v1, got {members}"


def test_web_recon_v1_retains_core_recon_tools():
    """回归：原有核心工具必须保留。"""
    from app.core.capability_kits import (
        get_kit_member_tools,
        reload_kit_registry_for_tests,
    )
    reload_kit_registry_for_tests()
    members = get_kit_member_tools("web-recon-v1")
    for core in ("katana", "dirsearch", "httpx", "nuclei", "curl-raw", "http-enum"):
        assert core in members, f"{core} missing from web-recon-v1 (got {members})"
