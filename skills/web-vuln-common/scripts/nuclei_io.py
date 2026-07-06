"""Summarize Nuclei JSONL output."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

_MAX_EVIDENCE_ITEMS = 6
_MAX_EVIDENCE_CHARS = 240

# (substring in template text, canonical stack label) — info/low 命中时写入 tech_stack 证据
_TECH_STACK_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("struts", "struts"),
    ("struts2", "struts"),
    ("apache struts", "struts"),
    ("spring framework", "spring"),
    ("spring mvc", "spring"),
    ("spring boot", "spring"),
    ("spring security", "spring"),
    ("apache tomcat", "tomcat"),
    ("tomcat", "tomcat"),
    ("jetty", "jetty"),
    ("weblogic", "weblogic"),
    ("oracle weblogic", "weblogic"),
    ("jboss", "jboss"),
    ("wildfly", "wildfly"),
    ("jenkins", "jenkins"),
    ("wordpress", "wordpress"),
    ("drupal", "drupal"),
    ("laravel", "laravel"),
    ("django", "django"),
    ("rails", "rails"),
    ("ruby on rails", "rails"),
    ("nodejs", "nodejs"),
    ("express", "express"),
    ("dotnet", "dotnet"),
    ("asp.net", "dotnet"),
    ("iis", "iis"),
    ("php", "php"),
    ("coldfusion", "coldfusion"),
)


def iter_nuclei_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        yield obj
                except Exception:
                    continue
    except Exception:
        return


def severity_rank(s: str) -> int:
    m = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    return m.get((s or "").lower(), 0)


def _info_tags_str(info: dict[str, Any]) -> str:
    t = info.get("tags")
    if isinstance(t, list):
        return ",".join(str(x) for x in t)
    return str(t or "")


def _row_text_blob(r: dict[str, Any], info: dict[str, Any]) -> str:
    parts = [
        str(info.get("name") or ""),
        str(info.get("description") or ""),
        _info_tags_str(info),
        str(info.get("template-id") or ""),
        str(r.get("template-id") or ""),
        str(info.get("reference") or ""),
        str(r.get("matcher-name") or ""),
    ]
    classification = info.get("classification")
    if isinstance(classification, dict):
        parts.append(str(classification.get("cve-id") or ""))
    return " ".join(parts).lower()


def _tech_signals_for_low_info_row(r: dict[str, Any], info: dict[str, Any]) -> list[str]:
    blob = _row_text_blob(r, info)
    out: list[str] = []
    for needle, label in _TECH_STACK_KEYWORDS:
        if needle in blob:
            if label not in out:
                out.append(label)
    return out


def _truncate_text(v: Any, max_chars: int = _MAX_EVIDENCE_CHARS) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    return s[:max_chars]


def _extract_evidence_items(row: dict[str, Any], max_items: int = _MAX_EVIDENCE_ITEMS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def push(v: Any) -> None:
        if len(out) >= max_items:
            return
        s = _truncate_text(v)
        if not s:
            return
        if s in seen:
            return
        seen.add(s)
        out.append(s)

    extracted = row.get("extracted-results")
    if isinstance(extracted, list):
        for item in extracted:
            push(item)
            if len(out) >= max_items:
                return out
    elif isinstance(extracted, str):
        push(extracted)

    for key in ("matcher-name", "matched-at", "url", "host", "ip"):
        push(row.get(key))
        if len(out) >= max_items:
            return out
    return out


def summarize_nuclei_jsonl(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, int], int, list[dict[str, Any]]]:
    """
    返回:
    - vulns: severity >= medium 的漏洞摘要（与历史行为一致）
    - hist: 各 severity 计数
    - low_info: info+low 行数
    - tech_stack_evidence: 来自 info/low 行、且模板文本命中技术栈关键词的证据（疑罪从有，供人工复核）
    """
    vulns: list[dict[str, Any]] = []
    tech_stack_evidence: list[dict[str, Any]] = []
    tech_seen: set[tuple[str, str, str]] = set()
    hist: dict[str, int] = {}
    low_info = 0
    for r in iter_nuclei_jsonl(path):
        info = r.get("info") if isinstance(r.get("info"), dict) else {}
        sev = str(info.get("severity") or r.get("severity") or "unknown").lower()
        hist[sev] = hist.get(sev, 0) + 1
        if sev in ("info", "low"):
            low_info += 1

        tmpl = str(info.get("template-id") or info.get("name") or r.get("template-id") or "")
        matched = str(r.get("matched-at") or r.get("host") or "")

        if severity_rank(sev) >= severity_rank("medium"):
            classification = info.get("classification") if isinstance(info.get("classification"), dict) else {}
            cve = classification.get("cve-id") or []
            if isinstance(cve, list):
                cve_s = ",".join(str(x) for x in cve[:3])
            else:
                cve_s = str(cve or "")
            vulns.append(
                {
                    "severity": sev,
                    "template_id": tmpl,
                    "url": matched,
                    "cve": cve_s,
                    "matcher": str((r.get("matcher-name") or info.get("matcher-name") or "")[:200]),
                    "template_path": str(r.get("template-path") or "")[:300],
                    "matched_at": str(r.get("matched-at") or "")[:300],
                    "evidence": _extract_evidence_items(r),
                }
            )
            continue

        # info / low / unknown：不进入 vulns，但抽取技术栈指纹信号
        for sig in _tech_signals_for_low_info_row(r, info):
            key = (sig, tmpl, matched)
            if key in tech_seen:
                continue
            tech_seen.add(key)
            tech_stack_evidence.append(
                {
                    "signal": sig,
                    "severity": sev,
                    "template_id": tmpl,
                    "url": matched,
                    "source": "nuclei_info_low",
                }
            )

    return vulns, hist, low_info, tech_stack_evidence


def extract_strict_key_findings(path: Path, *, max_findings: int = 40) -> dict[str, Any]:
    """
    严格关键结果提取：
    - 从 nuclei 原始 JSONL 行抽取稳定字段，形成可供编排器消费的高置信摘要；
    - 仅保留 high/critical 且含 URL 的命中，保证高价值结果不丢失。
    """
    findings: list[dict[str, Any]] = []
    critical_urls: set[str] = set()
    high_urls: set[str] = set()
    seen_findings: set[tuple[str, str, str]] = set()

    for r in iter_nuclei_jsonl(path):
        info = r.get("info") if isinstance(r.get("info"), dict) else {}
        sev = str(info.get("severity") or r.get("severity") or "unknown").strip().lower()
        if sev not in ("high", "critical"):
            continue

        template_id = str(r.get("template-id") or info.get("template-id") or info.get("name") or "").strip()
        url = str(r.get("matched-at", "") or r.get("url", "") or r.get("host", "")).strip()
        if not url:
            continue
        matcher = str(r.get("matcher-name", "") or info.get("matcher-name", "")).strip()
        evidence = _extract_evidence_items(r)
        if not evidence:
            # 兜底：缺少 extracted-results 等证据字段时，至少保留可定位线索
            fallback = str(r.get("matched-at", "") or r.get("url", "") or url).strip()
            evidence = [fallback[:_MAX_EVIDENCE_CHARS]] if fallback else [url[:_MAX_EVIDENCE_CHARS]]
        key = (template_id, url, sev)
        if key in seen_findings:
            continue
        seen_findings.add(key)

        findings.append(
            {
                "severity": sev,
                "template_id": template_id,
                "template_path": str(r.get("template-path") or "").strip()[:300],
                "url": url,
                "matcher": matcher[:200],
                "evidence": evidence[:_MAX_EVIDENCE_ITEMS],
            }
        )
        if sev == "critical" and url:
            critical_urls.add(url)
        elif sev == "high" and url:
            high_urls.add(url)
        if len(findings) >= max_findings:
            break

    high_value_facts: list[str] = []
    if critical_urls:
        high_value_facts.append(
            "[High-Value] Confirmed CRITICAL vulnerabilities on endpoints: "
            + ", ".join(sorted(critical_urls)[:8])
        )
    if high_urls:
        high_value_facts.append(
            "[High-Value] Confirmed HIGH vulnerabilities on endpoints: "
            + ", ".join(sorted(high_urls)[:8])
        )
    return {
        "key_findings": findings,
        "high_value_facts": high_value_facts,
    }


def merge_histograms(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def merge_tech_stack_evidence(
    a: list[dict[str, Any]], b: list[dict[str, Any]], *, limit: int = 200
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for block in (a, b):
        for item in block:
            if not isinstance(item, dict):
                continue
            sig = str(item.get("signal") or "")
            tid = str(item.get("template_id") or "")
            url = str(item.get("url") or "")
            k = (sig, tid, url)
            if k in seen:
                continue
            seen.add(k)
            out.append(item)
            if len(out) >= limit:
                return out
    return out
