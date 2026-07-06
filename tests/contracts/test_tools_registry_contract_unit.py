"""
R5b：tools_registry.yaml 与 Executor 解析字段对齐（单测门禁）。
"""
import importlib.util
import re
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import yaml

ROOT = REPO_ROOT


def _load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_registry_yaml_text() -> str:
    reg = ROOT / "docker" / "tools_registry.yaml"
    raw = reg.read_text(encoding="utf-8")
    tools_root = str(ROOT / "TRUSTGUARD_TOOLS_ROOT").replace("\\", "/")
    return re.sub(r"\$\{TRUSTGUARD_TOOLS_ROOT\}", tools_root, raw)


def test_tools_registry_yaml_passes_contract() -> None:
    val = _load_module("trustguard_registry_validation", "executor/app/registry_validation.py")
    data = yaml.safe_load(_load_registry_yaml_text()) or {}
    errs = val.registry_validation_errors(data)
    assert not errs, "tools_registry.yaml contract violations:\n" + "\n".join(errs)


def test_executor_tools_registry_module_loads_same_yaml() -> None:
    """Executor 侧 _load_registry 能解析当前仓库 YAML（与契约同源）。"""
    tr = _load_module("executor_tools_registry_contract", "executor/app/tools_registry.py")
    data = tr._load_registry()
    val = _load_module("trustguard_registry_validation_2", "executor/app/registry_validation.py")
    errs = val.registry_validation_errors(data)
    assert not errs, "\n".join(errs)


def test_get_tool_info_shape_for_registered_skills() -> None:
    tr = _load_module("executor_tools_registry_shape", "executor/app/tools_registry.py")
    ids = tr.list_registered_skills()
    assert ids, "expected at least one skill in tools_registry.yaml"
    for sid in ("nmap", "nuclei", "dispatcher", "curl-raw"):
        if sid not in ids:
            continue
        info = tr.get_tool_info(sid)
        assert info is not None, sid
        assert info["runner"] in ("python", "binary")
        assert isinstance(info.get("category"), str)
        assert isinstance(info.get("default_args"), list)
        assert "extract_kb_features" in info
        assert isinstance(info["extract_kb_features"], bool)
        assert "internal_agent_plane" in info
        assert isinstance(info["internal_agent_plane"], bool)
        if sid in ("nmap", "nuclei"):
            assert info["extract_kb_features"] is True
        else:
            assert info["extract_kb_features"] is False


def test_registry_rejects_nonbool_extract_kb_features() -> None:
    val = _load_module("trustguard_registry_validation_r5c", "executor/app/registry_validation.py")
    data = {
        "tools": {
            "bad-extract": {
                "category": "Search",
                "runner": "python",
                "path": "",
                "executable": "",
                "extract_kb_features": "yes",
            }
        }
    }
    errs = val.registry_validation_errors(data)
    assert any("extract_kb_features" in e for e in errs)


def test_get_tool_info_extract_true_custom_registry(tmp_path, monkeypatch) -> None:
    reg = tmp_path / "reg.yaml"
    reg.write_text(
        "tools:\n"
        "  test-kb-flag:\n"
        "    category: Search\n"
        "    path: ''\n"
        "    executable: ''\n"
        "    runner: python\n"
        "    extract_kb_features: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TOOLS_REGISTRY_YAML", str(reg))
    tr = _load_module("executor_tools_registry_r5c", "executor/app/tools_registry.py")
    info = tr.get_tool_info("test-kb-flag")
    assert info is not None
    assert info["extract_kb_features"] is True
