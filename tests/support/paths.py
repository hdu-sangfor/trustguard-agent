"""Shared test path helpers.

Tests are grouped below ``tests/`` by purpose, so individual files should not
derive the repository root from their own nesting depth.
"""
from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "docker-compose.yml").is_file() and (
            candidate / "orchestrator"
        ).is_dir():
            return candidate
    raise RuntimeError(f"could not locate repository root from {current}")


REPO_ROOT = find_repo_root()
ORCHESTRATOR_ROOT = REPO_ROOT / "orchestrator"
EXECUTOR_ROOT = REPO_ROOT / "executor"
EVIDENCE_ROOT = REPO_ROOT / "evidence"
GATEWAY_ROOT = REPO_ROOT / "gateway"
SKILLS_ROOT = REPO_ROOT / "skills"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
