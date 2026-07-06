"""skill_params_normalize：tags 归一化为 list[str]。"""

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest


@pytest.fixture
def norm_mod():
    root = str(REPO_ROOT / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, root)
    import importlib

    mod = importlib.import_module("app.core.skill_params_normalize")
    yield mod
    sys.path.remove(root)
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_normalize_comma_string(norm_mod):
    assert norm_mod.normalize_tags_to_list("struts, rce, xss") == ["struts", "rce", "xss"]


def test_normalize_python_repr_list_string(norm_mod):
    assert norm_mod.normalize_tags_to_list("['struts', 'rce']") == ["struts", "rce"]


def test_normalize_json_list_string(norm_mod):
    assert norm_mod.normalize_tags_to_list('["a","b"]') == ["a", "b"]


def test_normalize_list_passthrough(norm_mod):
    assert norm_mod.normalize_tags_to_list(["x", "y", "x"]) == ["x", "y"]


def test_tags_list_to_nuclei_cli(norm_mod):
    assert norm_mod.tags_list_to_nuclei_cli(["a", "b"]) == "a,b"
    assert norm_mod.tags_list_to_nuclei_cli([]) == ""
