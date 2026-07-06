"""R5c：KB extract_kb_features 注册表契约（编排侧）。"""
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ROOT = REPO_ROOT
_ORCH = _ROOT / "orchestrator"
if str(_ORCH) not in sys.path:
    sys.path.insert(0, str(_ORCH))


def test_extract_kb_features_true_for_nmap_kb_r1c(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLS_REGISTRY_YAML", str(_ROOT / "docker" / "tools_registry.yaml"))
    monkeypatch.setenv(
        "TRUSTGUARD_TOOLS_ROOT", str(_ROOT / "TRUSTGUARD_TOOLS_ROOT").replace("\\", "/")
    )
    from app.core.kb_feature_policy import extract_kb_features_declared

    assert extract_kb_features_declared("nmap") is True


def test_assert_vector_feature_write_blocks_without_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLS_REGISTRY_YAML", str(_ROOT / "docker" / "tools_registry.yaml"))
    monkeypatch.setenv(
        "TRUSTGUARD_TOOLS_ROOT", str(_ROOT / "TRUSTGUARD_TOOLS_ROOT").replace("\\", "/")
    )
    from app.core.kb_feature_policy import (
        KbFeaturePolicyError,
        assert_vector_feature_write_allowed,
    )

    with pytest.raises(KbFeaturePolicyError) as ei:
        assert_vector_feature_write_allowed("dispatcher")
    assert ei.value.code == "KB_FEATURE_EXTRACT_NOT_DECLARED"


def test_extract_true_when_registry_sets_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    from app.core.kb_feature_policy import (
        assert_vector_feature_write_allowed,
        extract_kb_features_declared,
    )

    assert extract_kb_features_declared("test-kb-flag") is True
    assert_vector_feature_write_allowed("test-kb-flag")  # does not raise
