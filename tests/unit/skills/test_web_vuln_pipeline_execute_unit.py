"""Unit tests for katana / dispatcher / nuclei skill helpers (split from former web-vuln-pipeline)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

ROOT = REPO_ROOT
_COMMON = ROOT / "skills" / "web-vuln-common" / "scripts"


def _ensure_common_path() -> None:
    p = str(_COMMON)
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_katana_execute():
    _ensure_common_path()
    p = ROOT / "skills" / "katana" / "scripts" / "execute.py"
    spec = importlib.util.spec_from_file_location("katana_execute", str(p))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _load_nuclei_execute():
    _ensure_common_path()
    saved_path = sys.path[:]
    try:
        for sub in ("nuclei/scripts", "nuclei/common"):
            sp = str(ROOT / "skills" / sub)
            if sp not in sys.path:
                sys.path.insert(0, sp)

        p = ROOT / "skills" / "nuclei" / "scripts" / "execute.py"
        spec = importlib.util.spec_from_file_location("nuclei_execute", str(p))
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        return mod
    finally:
        sys.path[:] = saved_path


def test_summarize_nuclei_jsonl_streaming(tmp_path):
    _ensure_common_path()
    from nuclei_io import summarize_nuclei_jsonl

    f = tmp_path / "nuclei.jsonl"
    f.write_text(
        "\n".join(
            [
                '{"info":{"severity":"info","name":"Apache Struts2 Showcase","template-id":"t-struts-info"},"matched-at":"http://a/struts"}',
                '{"info":{"severity":"low","template-id":"t-low"},"matched-at":"http://a"}',
                '{"info":{"severity":"high","template-id":"t-high","classification":{"cve-id":["CVE-1"]}},"matched-at":"http://b"}',
                '{"info":{"severity":"medium","template-id":"t-mid"},"matched-at":"http://c"}',
                "not-json",
            ]
        ),
        encoding="utf-8",
    )
    vulns, hist, low_info, tech = summarize_nuclei_jsonl(f)
    assert low_info == 2
    assert hist.get("info") == 1
    assert hist.get("low") == 1
    assert hist.get("high") == 1
    assert hist.get("medium") == 1
    assert len(vulns) == 2
    assert {v["template_id"] for v in vulns} == {"t-high", "t-mid"}
    struts_ev = [t for t in tech if t.get("signal") == "struts"]
    assert struts_ev
    assert struts_ev[0].get("source") == "nuclei_info_low"


def test_derive_stack_hint_from_fingerprint_rules():
    _ensure_common_path()
    from fingerprint_tags import derive_stack_hint, load_fingerprint_rules

    cfg = ROOT / "skills" / "dispatcher" / "config" / "fingerprint_tag_map.json"
    rules = load_fingerprint_rules(cfg)
    hint = derive_stack_hint(
        params={},
        context={"fingerprint": "Apache Tomcat / Spring Boot"},
        rules=rules,
    )
    assert hint == "java"


def test_derive_stack_hint_params_priority():
    _ensure_common_path()
    from fingerprint_tags import derive_stack_hint, load_fingerprint_rules

    cfg = ROOT / "skills" / "dispatcher" / "config" / "fingerprint_tag_map.json"
    rules = load_fingerprint_rules(cfg)
    hint = derive_stack_hint(
        params={"stack_hint": "php"},
        context={"fingerprint": "Apache Tomcat / Spring Boot"},
        rules=rules,
    )
    assert hint == "php"


def test_derive_stack_hint_regex_rule():
    _ensure_common_path()
    from fingerprint_tags import derive_stack_hint, load_fingerprint_rules

    cfg = ROOT / "skills" / "dispatcher" / "config" / "fingerprint_tag_map.json"
    rules = load_fingerprint_rules(cfg)
    hint = derive_stack_hint(
        params={},
        context={"whatweb": "Oracle WebLogic Server"},
        rules=rules,
    )
    assert hint == "java"


def test_fingerprint_rules_sorted_by_priority():
    _ensure_common_path()
    from fingerprint_tags import load_fingerprint_rules

    cfg = ROOT / "skills" / "dispatcher" / "config" / "fingerprint_tag_map.json"
    rules = load_fingerprint_rules(cfg)
    assert isinstance(rules, list) and rules
    priorities = [int(r.get("priority") or 0) for r in rules]
    assert priorities == sorted(priorities, reverse=True)


def test_resolve_nuclei_templates_dir_from_env(monkeypatch, tmp_path):
    mod = _load_nuclei_execute()
    d = tmp_path / "temlists"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sample.yaml").write_text("id: t1\ninfo:\n  name: t1\n", encoding="utf-8")
    monkeypatch.setenv("NUCLEI_TEMPLATES_DIR", str(d))
    assert mod._resolve_nuclei_templates_dir() == str(d.resolve())


def test_katana_urls_plain_and_jsonl(tmp_path):
    _ensure_common_path()
    from katana_io import katana_urls_from_file

    f = tmp_path / "katana_urls.txt"
    f.write_text(
        "http://127.0.0.1:8080/\n"
        '{"request":{"method":"GET","url":"http://127.0.0.1:8080/doUpload.action"},"timestamp":"1"}\n',
        encoding="utf-8",
    )
    urls = katana_urls_from_file(f)
    assert "http://127.0.0.1:8080/" in urls
    assert any("doUpload.action" in u for u in urls)


def test_katana_urls_json_slash_escape_no_duplicate(tmp_path):
    _ensure_common_path()
    from katana_io import katana_urls_from_file

    f = tmp_path / "katana_urls.txt"
    f.write_text(
        '{"request":{"method":"GET","url":"http://127.0.0.1:8080\\/"}}',
        encoding="utf-8",
    )
    urls = katana_urls_from_file(f)
    assert len([u for u in urls if "127.0.0.1:8080" in u]) == 1
    assert not any("\\\\" in u or (u.endswith("\\/")) for u in urls)


def test_katana_urls_embedded_html_form_action_matrix_params(tmp_path):
    _ensure_common_path()
    from katana_io import katana_urls_from_file

    html = '<html><body><form action="/doUpload.action;jsessionid=ABC123" method="post"></form></body></html>'
    line = json.dumps(
        {"request": {"method": "GET", "url": "http://127.0.0.1:8080/welcome.do"}, "page": html}
    )
    f = tmp_path / "katana_urls.txt"
    f.write_text(line, encoding="utf-8")
    urls = katana_urls_from_file(f, fallback_base="http://127.0.0.1:8080/")
    hit = [u for u in urls if "doUpload.action" in u]
    assert hit
    assert all("jsessionid" not in u.lower() for u in hit)


def test_merge_wordlist_arg(tmp_path):
    mod = _load_katana_execute()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")
    assert mod._merge_wordlist_arg(str(a), [str(b)]) == f"{a},{b}"
    assert mod._merge_wordlist_arg(None, [str(b)]) == str(b)


def test_parse_katana_known_files():
    mod = _load_katana_execute()
    assert mod._parse_katana_known_files("robotstxt,sitemapxml") == ["robotstxt", "sitemapxml"]
    assert mod._parse_katana_known_files("all,robotstxt") == ["all", "robotstxt"]
    assert mod._parse_katana_known_files("none") == []
