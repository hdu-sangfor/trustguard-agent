"""Unit tests for shared web-vuln ETL helpers (web-vuln-common)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from tests.paths import REPO_ROOT

ROOT = REPO_ROOT
_WVC_ETL = ROOT / "skills" / "web-vuln-common" / "scripts" / "etl.py"
_spec = importlib.util.spec_from_file_location("web_vuln_common_etl", _WVC_ETL)
assert _spec and _spec.loader
_wvc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wvc)

DETERMINISM_VERSION = _wvc.DETERMINISM_VERSION
canonicalize_url = _wvc.canonicalize_url
merge_and_refine = _wvc.merge_and_refine
normalize_for_dedup = _wvc.normalize_for_dedup


def test_determinism_version() -> None:
    assert DETERMINISM_VERSION == "etl-4"


def test_canonicalize_url_strips_json_slash_escapes() -> None:
    # 根路径规范化为带尾部 /
    assert canonicalize_url("http://127.0.0.1:8080\\/") == "http://127.0.0.1:8080/"
    assert "\\" not in canonicalize_url("http://127.0.0.1:8080\\/")


def test_normalize_preserves_action_values() -> None:
    a = normalize_for_dedup("http://x.com/a?action=delete&id=1")
    b = normalize_for_dedup("http://x.com/a?action=view&id=2")
    assert a != b


def test_merge_scope_and_seed() -> None:
    urls, stats = merge_and_refine(
        ["http://example.com/p"],
        ["http://example.com/q"],
        max_urls=100,
        scope_hosts={"example.com"},
        seed_urls=["http://example.com/seed"],
    )
    assert "http://example.com/seed" in urls or any("seed" in u for u in urls)
    assert stats["raw_in"] >= 3


def test_merge_scope_strict_host_rejects_subdomain_by_default() -> None:
    urls, stats = merge_and_refine(
        ["http://sub.example.com/p"],
        [],
        max_urls=100,
        scope_hosts={"example.com"},
    )
    assert stats["dropped_scope"] >= 1
    assert not urls


def test_merge_scope_subdomain_opt_in() -> None:
    urls, stats = merge_and_refine(
        ["http://sub.example.com/p"],
        [],
        max_urls=100,
        scope_hosts={"example.com"},
        allow_scope_subdomains=True,
    )
    assert urls
    assert stats["dropped_scope"] == 0


def test_external_cdn_dropped_when_scope_is_internal_alias() -> None:
    urls, stats = merge_and_refine(
        ["https://e.topthink.com/vuln/path"],
        [],
        max_urls=100,
        scope_hosts={"host.docker.internal"},
    )
    assert stats["dropped_scope"] >= 1
    assert not urls
