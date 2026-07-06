"""
KB-R1c：从 nuclei 汇总 artifacts 抽取模板/严重度微特征（不重嵌套全量 JSON）。
"""

from __future__ import annotations

from typing import Any, Dict, List


def _collect_vulns(artifacts: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = artifacts.get("vulnerabilities")
    out: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        out.extend(v for v in raw if isinstance(v, dict))
    lr = artifacts.get("llm_ready")
    if isinstance(lr, dict):
        raw2 = lr.get("vulnerabilities")
        if isinstance(raw2, list):
            out.extend(v for v in raw2 if isinstance(v, dict))
    return out


def extract_kb_features(artifacts: Dict[str, Any] | None, context: Dict[str, Any] | None) -> Dict[str, Any]:
    a = artifacts if isinstance(artifacts, dict) else {}
    vulns = _collect_vulns(a)

    template_ids: list[str] = []
    severities: list[str] = []
    for v in vulns:
        tid = str(v.get("template_id") or v.get("template-id") or v.get("id") or "").strip()
        if tid:
            template_ids.append(tid)
        sev = str(v.get("severity") or "").strip().lower()
        if sev:
            severities.append(sev)

    hist = a.get("severity_histogram")
    if not isinstance(hist, dict) and isinstance(a.get("llm_ready"), dict):
        hist = (a.get("llm_ready") or {}).get("severity_histogram")
    hist_out = hist if isinstance(hist, dict) else {}

    tid_unique = list(dict.fromkeys(template_ids))[:32]
    crit_high = sum(1 for s in severities if s in ("critical", "high"))

    intent_parts = [f"nuclei findings={len(vulns)}", f"templates={len(tid_unique)}"]
    if crit_high:
        intent_parts.append(f"critical_high={crit_high}")
    if tid_unique:
        intent_parts.append("tpl=" + ",".join(tid_unique[:6]))
    intent_projection = " ".join(intent_parts)[:320]

    return {
        "skill_id": "nuclei",
        "vulnerability_count": len(vulns),
        "template_id_head": tid_unique[:24],
        "severity_histogram": {k: hist_out[k] for k in list(hist_out.keys())[:16]},
        "critical_high_count": crit_high,
        "intent_projection": intent_projection,
    }
