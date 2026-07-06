from __future__ import annotations

import os
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core.capability_kit_assembler import (  # noqa: E402
    build_kit_manual_block,
    clear_kit_manual_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _clear_manual_cache() -> None:
    clear_kit_manual_cache_for_tests()
    yield
    clear_kit_manual_cache_for_tests()


def test_build_kit_manual_block_non_empty_for_real_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = REPO_ROOT
    monkeypatch.setenv("TRUSTGUARD_REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCH_KIT_MANUAL_ENABLED", "1")
    block = build_kit_manual_block("web-recon-v1", ["httpx"])
    assert "httpx" in block.lower() or len(block) > 10


def test_default_repo_root_finds_skills_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRUSTGUARD_REPO_ROOT", raising=False)
    from app.core import capability_kit_assembler as m

    skills = m._skills_dir()
    assert skills.name == "skills"
    assert (skills / "httpx").is_dir()


def test_trustguard_repo_root_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    monkeypatch.setenv("TRUSTGUARD_REPO_ROOT", str(repo))
    from app.core import capability_kit_assembler as m

    assert m._skills_dir() == repo / "skills"
