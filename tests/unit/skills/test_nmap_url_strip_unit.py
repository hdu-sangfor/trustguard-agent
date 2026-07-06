"""nmap execute.py: URL stripping — nmap must receive bare host/IP, not http(s) URLs."""
import importlib.util
from pathlib import Path
from tests.paths import REPO_ROOT


def _load_nmap_execute():
    script = REPO_ROOT / "skills" / "nmap" / "scripts" / "execute.py"
    spec = importlib.util.spec_from_file_location("nmap_execute", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_strip_http_url():
    mod = _load_nmap_execute()
    assert mod._strip_url_to_host("http://host.docker.internal:8080") == "host.docker.internal"


def test_strip_https_url_with_path():
    mod = _load_nmap_execute()
    assert mod._strip_url_to_host("https://example.com/some/path?q=1") == "example.com"


def test_bare_host_unchanged():
    mod = _load_nmap_execute()
    assert mod._strip_url_to_host("host.docker.internal") == "host.docker.internal"


def test_bare_ip_unchanged():
    mod = _load_nmap_execute()
    assert mod._strip_url_to_host("192.168.1.1") == "192.168.1.1"


def test_empty_target():
    mod = _load_nmap_execute()
    assert mod._strip_url_to_host("") == ""


def test_build_cmd_uses_stripped_host(monkeypatch):
    mod = _load_nmap_execute()
    # Patch _resolve_executable so we don't need nmap installed
    monkeypatch.setattr(mod, "_resolve_executable", lambda info: ("nmap", None))
    cmd = mod._build_cmd("http://host.docker.internal:8080", {}, {})
    # nmap target must be bare hostname, not the full URL
    assert cmd[-1] == "host.docker.internal"
    assert "http://" not in " ".join(cmd)
