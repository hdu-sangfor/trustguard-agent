"""Unit tests for orchestrator recon_dirsearch_seeds."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest


def _orch_path() -> str:
    return str(REPO_ROOT / "orchestrator")


@pytest.fixture
def seeds_mod():
    root = _orch_path()
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, root)
    import importlib

    mod = importlib.import_module("app.core.recon_dirsearch_seeds")
    yield mod
    sys.path.remove(root)
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_katana_seeds_from_dirsearch_json_redirect_and_403(seeds_mod, tmp_path: Path) -> None:
    p = tmp_path / "dirsearch.json"
    p.write_text(
        json.dumps(
            {
                "results": [
                    {"url": "http://x/robots.txt", "status": 404},
                    {"url": "http://x/app/", "status": 301},
                    {"url": "http://x/showcase.jsp", "status": 200},
                    {"url": "http://x/api/foo.action", "status": 200},
                    {"url": "http://x/a.css", "status": 200},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seeds = seeds_mod.katana_seeds_from_dirsearch_json(p, max_seeds=10)
    assert "http://x/app/" in seeds
    assert "http://x/showcase.jsp" in seeds
    assert "http://x/api/foo.action" not in seeds
    assert "http://x/a.css" not in seeds


def test_katana_seeds_empty_file(seeds_mod, tmp_path: Path) -> None:
    assert seeds_mod.katana_seeds_from_dirsearch_json(tmp_path / "missing.json") == []
