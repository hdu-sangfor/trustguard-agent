"""Unit tests for skills/web-vuln-common/scripts/etl.py (split pipeline)."""
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

ROOT = REPO_ROOT
sys.path.insert(0, str(ROOT / "skills" / "web-vuln-common" / "scripts"))

from etl import (  # noqa: E402
    DETERMINISM_VERSION,
    chunk_urls,
    path_template_key,
    strip_matrix_params_from_url,
    template_dedupe_representatives,
)


def test_determinism_version():
    assert DETERMINISM_VERSION == "etl-4"


def test_path_template_key_collapses_numeric_segments():
    a = "http://x.example/user/1/detail"
    b = "http://x.example/user/2/detail"
    assert path_template_key(a) == path_template_key(b)


def test_strip_matrix_params():
    u = "http://x.example/app;jsessionid=ABC123/foo"
    out = strip_matrix_params_from_url(u)
    assert ";jsessionid" not in (out.split("://", 1)[-1].lower())


def test_strip_matrix_params_cluster_style_session():
    u = "http://x.example/doUpload.action;jsessionid=ab-cd.node1!x/foo"
    out = strip_matrix_params_from_url(u)
    assert ";jsessionid" not in out.lower()


def test_template_dedupe_representatives():
    urls = [
        "http://a.example/x/1",
        "http://a.example/x/2",
        "http://a.example/y/static",
    ]
    reps, n = template_dedupe_representatives(urls)
    assert n == 1
    assert len(reps) == 2


def test_chunk_urls_size():
    u = [f"http://t.example/p{i}" for i in range(45)]
    parts = chunk_urls(u, 20)
    assert len(parts) == 3
    assert len(parts[0]) == 20
    assert len(parts[1]) == 20
    assert len(parts[2]) == 5


def test_nuclei_list_guard_contract():
    """Mirror nuclei skill guard: refuse empty file and non-URL lines."""
    from urllib.parse import urlparse

    def validate(lines: list[str]) -> bool:
        if not lines:
            return False
        for ln in lines:
            if not ln.startswith(("http://", "https://")):
                return False
            urlparse(ln)
        return True

    assert validate(["http://a/x"])
    assert not validate([])
    assert not validate(["ftp://x"])
