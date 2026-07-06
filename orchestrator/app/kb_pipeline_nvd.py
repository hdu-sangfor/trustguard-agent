"""
kb-r2c：NVD CVE API 2.0 响应解析（不含网络层）。
"""

from __future__ import annotations

from typing import Any


def _english_description(cve_block: dict[str, Any]) -> str:
    for d in cve_block.get("descriptions") or []:
        if not isinstance(d, dict):
            continue
        if str(d.get("lang") or "").lower() == "en":
            v = d.get("value")
            if isinstance(v, str) and v.strip():
                return v.strip()
    for d in cve_block.get("descriptions") or []:
        if isinstance(d, dict):
            v = d.get("value")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _cvss_base_score(cve_block: dict[str, Any]) -> float | None:
    metrics = cve_block.get("metrics")
    if not isinstance(metrics, dict):
        return None
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        arr = metrics.get(key)
        if not isinstance(arr, list) or not arr:
            continue
        m0 = arr[0]
        if not isinstance(m0, dict):
            continue
        cd = m0.get("cvssData")
        if isinstance(cd, dict):
            try:
                return float(cd.get("baseScore"))
            except (TypeError, ValueError):
                pass
    return None


def _published_iso(cve_block: dict[str, Any]) -> str:
    # NVD 2.0: cve.published
    v = cve_block.get("published")
    return v.strip() if isinstance(v, str) else ""


def parse_nvd_cve_response(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    解析 NVD `.../cves/2.0` JSON，返回可入库记录列表。
    每项：cve_id, summary, embed_text, snapshot(dict)
    """
    if not isinstance(doc, dict):
        return []
    vulns = doc.get("vulnerabilities")
    if not isinstance(vulns, list):
        return []

    out: list[dict[str, Any]] = []
    for item in vulns:
        if not isinstance(item, dict):
            continue
        cve = item.get("cve")
        if not isinstance(cve, dict):
            continue
        cid = str(cve.get("id") or "").strip()
        if not cid:
            continue
        desc = _english_description(cve)
        summary = desc[:12000] if desc else cid
        embed = f"{cid}\n{desc}"[:8000] if desc else cid
        snap: dict[str, Any] = {
            "cve_id": cid,
            "published": _published_iso(cve),
        }
        score = _cvss_base_score(cve)
        if score is not None:
            snap["cvss_base_score"] = score
        out.append(
            {
                "cve_id": cid,
                "summary": summary,
                "embed_text": embed,
                "snapshot": snap,
            }
        )
    return out
