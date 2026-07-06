"""orchestrator http_enum_seeds：从 http-enum 页面提取 dispatcher 种子 URL。"""

import importlib
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_mod():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.core.http_enum_seeds")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_extract_form_action_strips_jsessionid():
    mod = _load_mod()
    html = (
        '<form action="/doUpload.action;jsessionid=ABC123" method="post">'
        "</form>"
    )
    tc = {
        "http-enum_url": "http://host.docker.internal:8080/",
        "http-enum_raw_preview": "HTTP/1.1 200 OK\n\n" + html,
    }
    urls = mod.extract_http_enum_seed_urls(tc)
    assert urls
    assert "doUpload.action" in urls[0]
    assert "jsessionid" not in urls[0].lower()


def test_jsessionid_cluster_style_id():
    mod = _load_mod()
    html = '<form action="/x;jsessionid=ab-cd.node1!123" method="post"></form>'
    tc = {
        "http-enum_url": "http://host:8080/",
        "http-enum_raw_preview": html,
    }
    urls = mod.extract_http_enum_seed_urls(tc)
    assert urls
    assert "jsessionid" not in urls[0].lower()


def test_relative_action_app_path_without_trailing_slash():
    """base 为 http://target/app 时，doUpload.action 需落在 /app/ 下，不能拼成 /appdoUpload 或 /doUpload。"""
    mod = _load_mod()
    html = '<form action="doUpload.action" method="post"></form>'
    tc = {
        "http-enum_url": "http://target:8080/app",
        "http-enum_raw_preview": html,
    }
    urls = mod.extract_http_enum_seed_urls(tc)
    assert urls == ["http://target:8080/app/doUpload.action"]


def test_relative_action_resolves_against_file_document():
    mod = _load_mod()
    html = '<form action="doUpload.action" method="post"></form>'
    tc = {
        "http-enum_url": "http://target:8080/dir/page.html",
        "http-enum_raw_preview": html,
    }
    urls = mod.extract_http_enum_seed_urls(tc)
    assert urls == ["http://target:8080/dir/doUpload.action"]


def test_strip_session_noise_from_url_query_phpsessid():
    mod = _load_mod()
    u = "http://x/a/b.do;jsessionid=OLD?PHPSESSID=abc&keep=1"
    out = mod.strip_session_noise_from_url(u)
    assert "jsessionid" not in out.lower()
    assert "phpsessid" not in out.lower()
    assert "keep=1" in out


def test_normalize_url_strips_tracking_cache_sorts_query():
    mod = _load_mod()
    u = "http://host/path?b=2&a=1&utm_source=x&_t=123&cb=9"
    out = mod.normalize_url_for_pipeline(u)
    assert "utm_" not in out.lower()
    assert "_t=" not in out.lower()
    assert "cb=" not in out.lower()
    assert "a=1" in out
    assert "b=2" in out
    assert out.index("a=1") < out.index("b=2")


def test_normalize_url_strips_csrf():
    mod = _load_mod()
    u = "http://h/x?csrf_token=abc&id=1"
    out = mod.normalize_url_for_pipeline(u)
    assert "csrf" not in out.lower()
    assert "id=1" in out


def test_normalize_strict_off_preserves_cache_like_params(monkeypatch):
    mod = _load_mod()
    monkeypatch.setenv("ORCHESTRATOR_NORMALIZE_STRICT", "0")
    u = "http://h/page?t=signature&cb=1"
    out = mod.normalize_url_for_pipeline(u)
    assert "t=signature" in out
    assert "cb=1" in out
