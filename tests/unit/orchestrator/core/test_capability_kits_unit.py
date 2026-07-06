from __future__ import annotations

import json

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core import capability_kits  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_CAPABILITY_KITS_PATH", raising=False)
    capability_kits.reload_kit_registry_for_tests()
    yield
    capability_kits.reload_kit_registry_for_tests()


def test_builtin_web_recon_kit_contains_httpx() -> None:
    tools = capability_kits.get_kit_member_tools("web-recon-v1")
    assert isinstance(tools, list)
    assert "httpx" in tools


def test_custom_json_may_declare_allowed_phases(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    p = tmp_path / "kits.json"
    p.write_text(
        json.dumps({"strict-kit": {"tools": ["httpx"], "allowed_phases": ["RECON", "THREAT_MODEL"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCH_CAPABILITY_KITS_PATH", str(p))
    capability_kits.reload_kit_registry_for_tests()
    ph = capability_kits.get_kit_phase_allowlist("strict-kit")
    assert ph == frozenset({"RECON", "THREAT_MODEL"})
    assert capability_kits.get_kit_member_tools("strict-kit") == ["httpx"]


def test_custom_json_overrides_builtin_tools(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    p = tmp_path / "kits.json"
    p.write_text(json.dumps({"web-recon-v1": {"tools": ["httpx", "katana"]}}), encoding="utf-8")
    monkeypatch.setenv("ORCH_CAPABILITY_KITS_PATH", str(p))
    capability_kits.reload_kit_registry_for_tests()
    assert capability_kits.get_kit_member_tools("web-recon-v1") == ["httpx", "katana"]


# ---------------------------------------------------------------------------
# v1-a249: mtime-based hotreload
# ---------------------------------------------------------------------------

import os
import time


class TestMtimeHotreload:
    """文件 mtime 变更时自动刷新注册表缓存。"""

    def test_reload_on_file_content_change(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """文件内容变更（mtime 变化）后下次加载自动刷新。"""
        p = tmp_path / "kits.json"
        p.write_text(json.dumps({"kit-a": {"tools": ["tool-1"]}}), encoding="utf-8")
        monkeypatch.setenv("ORCH_CAPABILITY_KITS_PATH", str(p))
        capability_kits.reload_kit_registry_for_tests()

        assert capability_kits.get_kit_member_tools("kit-a") == ["tool-1"]
        assert capability_kits.get_kit_member_tools("kit-b") is None

        # 确保 mtime 可见地变化（某些文件系统精度为 1 秒）
        time.sleep(0.05)
        p.write_text(json.dumps({"kit-b": {"tools": ["tool-2"]}}), encoding="utf-8")
        # 不调用 reload_kit_registry_for_tests()，依赖 mtime 检测
        assert capability_kits.get_kit_member_tools("kit-b") == ["tool-2"]

    def test_no_reload_when_mtime_unchanged(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """mtime 未变时返回缓存结果。"""
        p = tmp_path / "kits.json"
        p.write_text(json.dumps({"kit-c": {"tools": ["tool-c"]}}), encoding="utf-8")
        monkeypatch.setenv("ORCH_CAPABILITY_KITS_PATH", str(p))
        capability_kits.reload_kit_registry_for_tests()

        t1 = capability_kits.get_kit_member_tools("kit-c")
        t2 = capability_kits.get_kit_member_tools("kit-c")
        assert t1 == t2 == ["tool-c"]

    def test_no_path_uses_builtin_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 ORCH_CAPABILITY_KITS_PATH 时仅加载内置。"""
        monkeypatch.delenv("ORCH_CAPABILITY_KITS_PATH", raising=False)
        capability_kits.reload_kit_registry_for_tests()

        assert capability_kits.get_kit_member_tools("web-recon-v1") is not None
        # 二次调用应命中缓存
        assert capability_kits.get_kit_member_tools("web-recon-v1") is not None

    def test_file_deleted_after_load_falls_back_to_builtin(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """文件加载后被删除，下次 mtime 检查失败应回退到内置。"""
        p = tmp_path / "kits.json"
        p.write_text(json.dumps({"kit-d": {"tools": ["tool-d"]}}), encoding="utf-8")
        monkeypatch.setenv("ORCH_CAPABILITY_KITS_PATH", str(p))
        capability_kits.reload_kit_registry_for_tests()

        assert capability_kits.get_kit_member_tools("kit-d") == ["tool-d"]

        # 删除文件 → mtime 变为 None → 触发重载
        os.remove(p)
        result = capability_kits.get_kit_member_tools("kit-d")
        assert result is None  # 不再存在（仅内置 kit 可用）
        assert capability_kits.get_kit_member_tools("web-recon-v1") is not None


def test_pick_kit_anchor_skill_first_kit_order_in_available() -> None:
    """按 Kit 定义顺序取第一个落在 available 中的成员。"""
    anchor = capability_kits.pick_kit_anchor_skill("web-recon-v1", ["httpx", "katana"])
    assert anchor == "katana"


def test_pick_kit_anchor_skill_no_intersection_returns_none() -> None:
    assert capability_kits.pick_kit_anchor_skill("web-recon-v1", ["nmap"]) is None
