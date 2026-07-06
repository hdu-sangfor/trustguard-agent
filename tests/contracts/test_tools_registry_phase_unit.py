"""Executor tools_registry：阶段与 category 映射（大小写/下划线规范化）。"""

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest


@pytest.fixture
def tr_mod():
    root = str(REPO_ROOT / "executor")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, root)
    import importlib

    mod = importlib.import_module("app.tools_registry")
    yield mod
    sys.path.remove(root)
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_normalize_merges_case_and_underscore(tr_mod):
    assert tr_mod._normalize_category_token("VulnScan") == tr_mod._normalize_category_token("vuln_scan")
    assert tr_mod._normalize_category_token("Exploit") == tr_mod._normalize_category_token("exploit")
    assert tr_mod._normalize_category_token("Recon") == tr_mod._normalize_category_token("recon")


def test_curl_raw_in_recon_vuln_exploit(tr_mod):
    ids_r = tr_mod.get_skill_ids_for_phase("RECON")
    ids_v = tr_mod.get_skill_ids_for_phase("VULN_SCAN")
    ids_e = tr_mod.get_skill_ids_for_phase("EXPLOIT")
    assert "curl-raw" in ids_r
    assert "curl-raw" in ids_v
    assert "curl-raw" in ids_e


def test_dispatcher_in_recon_and_vuln_scan(tr_mod):
    ids_r = tr_mod.get_skill_ids_for_phase("RECON")
    ids_v = tr_mod.get_skill_ids_for_phase("VULN_SCAN")
    assert "dispatcher" in ids_r
    assert "dispatcher" in ids_v


def test_nuclei_in_vuln_scan_and_exploit(tr_mod):
    """nuclei now available in both VulnScan (general scanning) and Exploit (targeted verification)."""
    ids_v = tr_mod.get_skill_ids_for_phase("VULN_SCAN")
    ids_e = tr_mod.get_skill_ids_for_phase("EXPLOIT")
    assert "nuclei" in ids_v
    assert "nuclei" in ids_e

