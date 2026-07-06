"""
从 config/skill_policies.yaml 加载 Nuclei 等策略；缺失时回退到内置默认，避免启动失败。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[misc, assignment]

_POLICY_CACHE: dict[str, Any] | None = None


def _default_config_path() -> Path:
    # source tree: orchestrator/app/core/ -> orchestrator/config/
    # container:   /srv/orchestrator/app/core/ -> /srv/orchestrator/config/
    return Path(__file__).resolve().parents[2] / "config" / "skill_policies.yaml"


def _embedded_fallback() -> dict[str, Any]:
    """与 config/skill_policies.yaml 同步；文件不可读时使用。"""
    return {
        "version": 1,
        "nuclei": {
            "iml_tag_sets": {
                1: ["network", "ssl", "tls", "weak-cipher"],
                2: ["tech-stack", "tech", "exposure", "waf"],
                3: ["cve", "exposure", "misconfig", "vuln"],
                4: ["cve", "exposure", "misconfig", "vuln", "rce", "sqli", "xss"],
            },
            "sniper_boost_tags": ["rce", "fileupload", "misconfig", "cve", "exposure"],
            "tech_tag_mapping": [{"match": "struts", "tags": ["struts", "apache", "java"]}],
            "path_url_hints": [{"contains": ".action", "tags": ["struts", "apache", "java"]}],
        },
    }


def load_skill_policies() -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    if yaml is None:
        _POLICY_CACHE = _embedded_fallback()
        return _POLICY_CACHE

    path = (os.getenv("ORCHESTRATOR_SKILL_POLICIES_YAML") or "").strip()
    p = Path(path) if path else _default_config_path()
    data: dict[str, Any] = {}
    if p.is_file():
        try:
            raw = p.read_text(encoding="utf-8")
            if yaml:
                loaded = yaml.safe_load(raw)
                if isinstance(loaded, dict):
                    data = loaded
        except OSError:
            data = {}
    if not data:
        data = _embedded_fallback()

    _POLICY_CACHE = data
    return data


def reload_skill_policies_for_tests() -> None:
    """单元测试用。"""
    global _POLICY_CACHE
    _POLICY_CACHE = None


@dataclass(frozen=True)
class NucleiPolicyView:
    iml_tag_sets: dict[int, list[str]]
    sniper_boost_tags: tuple[str, ...]
    tech_tag_mapping: tuple[tuple[str, tuple[str, ...]], ...]
    path_url_hints: tuple[tuple[str, frozenset[str]], ...]


def get_nuclei_policy() -> NucleiPolicyView:
    root = load_skill_policies()
    nuc = root.get("nuclei") if isinstance(root.get("nuclei"), dict) else {}
    raw_iml = nuc.get("iml_tag_sets") or {}
    iml: dict[int, list[str]] = {}
    for k, v in raw_iml.items():
        try:
            ik = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, list):
            iml[ik] = [str(x).strip() for x in v if str(x).strip()]
    for i in range(1, 5):
        iml.setdefault(i, iml.get(2, ["tech-stack", "tech", "exposure", "waf"]))

    boost = nuc.get("sniper_boost_tags") or []
    boost_t = tuple(str(x).strip() for x in boost if str(x).strip())

    tech_rules: list[tuple[str, tuple[str, ...]]] = []
    for row in nuc.get("tech_tag_mapping") or []:
        if not isinstance(row, dict):
            continue
        m = str(row.get("match") or "").strip().lower()
        tags = row.get("tags") or []
        if not m or not isinstance(tags, list):
            continue
        tech_rules.append((m, tuple(str(t).strip() for t in tags if str(t).strip())))

    path_rules: list[tuple[str, frozenset[str]]] = []
    for row in nuc.get("path_url_hints") or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get("contains") or "").strip()
        tags = row.get("tags") or []
        if not c or not isinstance(tags, list):
            continue
        path_rules.append((c, frozenset(str(t).strip() for t in tags if str(t).strip())))

    return NucleiPolicyView(
        iml_tag_sets=iml,
        sniper_boost_tags=boost_t,
        tech_tag_mapping=tuple(tech_rules),
        path_url_hints=tuple(path_rules),
    )
