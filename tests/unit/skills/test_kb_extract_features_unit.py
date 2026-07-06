"""KB-R1c：nmap / nuclei kb_features.extract_kb_features 行为。"""

import importlib.util
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

_ROOT = REPO_ROOT


def _load_kb_module(relpath: str):
    p = _ROOT / relpath
    spec = importlib.util.spec_from_file_location(f"kb_feat_{relpath.replace('/', '_')}", str(p))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_nmap_kb_features_shape():
    mod = _load_kb_module("skills/nmap/kb_features.py")
    out = mod.extract_kb_features(
        {"open_ports": [22, 80], "services": [{"port": 80, "name": "http"}]},
        {},
    )
    assert out["skill_id"] == "nmap"
    assert 80 in (out.get("open_ports_head") or [])
    assert "http" in " ".join(out.get("service_names_head") or [])
    assert "intent_projection" in out


def test_nuclei_kb_features_from_vulns():
    mod = _load_kb_module("skills/nuclei/kb_features.py")
    vulns = [
        {"template_id": "x/ssrf", "severity": "high"},
        {"template_id": "y/rce", "severity": "critical"},
    ]
    out = mod.extract_kb_features({"vulnerabilities": vulns, "severity_histogram": {"high": 1}}, {})
    assert out["skill_id"] == "nuclei"
    assert out["vulnerability_count"] == 2
    assert any("ssrf" in t for t in (out.get("template_id_head") or []))
    assert out.get("critical_high_count", 0) >= 1
