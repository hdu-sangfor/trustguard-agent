"""
kb-r2c：CVE（NVD）同步与 Blog URL 抓取入库；独立环境开关，可配后台间隔或 HTTP 触发。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NVD_CVE_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def kb_pipeline_cve_enabled() -> bool:
    v = (os.getenv("KB_PIPELINE_CVE_ENABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def kb_pipeline_blog_enabled() -> bool:
    v = (os.getenv("KB_PIPELINE_BLOG_ENABLED") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def kb_cve_sync_interval_seconds() -> int:
    return max(0, int(os.getenv("KB_CVE_SYNC_INTERVAL_SECONDS", "0") or "0"))


def kb_blog_fetch_interval_seconds() -> int:
    return max(0, int(os.getenv("KB_BLOG_FETCH_INTERVAL_SECONDS", "0") or "0"))


def kb_blog_source_urls() -> list[str]:
    raw = (os.getenv("KB_BLOG_SOURCE_URLS") or "").strip()
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


def kb_cve_nvd_lookback_hours() -> int:
    try:
        return max(1, min(int(os.getenv("KB_CVE_NVD_LOOKBACK_HOURS", "168") or "168"), 24 * 90))
    except ValueError:
        return 168


def kb_cve_nvd_page_size() -> int:
    try:
        return max(1, min(int(os.getenv("KB_CVE_NVD_PAGE_SIZE", "30") or "30"), 2000))
    except ValueError:
        return 30


def kb_cve_max_upserts_per_run() -> int:
    try:
        return max(1, min(int(os.getenv("KB_CVE_NVD_MAX_UPSERTS", "50") or "50"), 500))
    except ValueError:
        return 50


def kb_blog_max_response_bytes() -> int:
    try:
        return max(10_000, min(int(os.getenv("KB_BLOG_MAX_RESPONSE_BYTES", "2000000") or "2000000"), 20_000_000))
    except ValueError:
        return 2_000_000


def _iso_last_mod_start() -> str:
    h = kb_cve_nvd_lookback_hours()
    cut = datetime.now(timezone.utc) - timedelta(hours=h)
    return cut.strftime("%Y-%m-%dT%H:%M:%S.000")


async def run_cve_nvd_sync_once(*, force: bool = False) -> dict[str, Any]:
    from app.clients.kb_client import QdrantKBClient, get_kb_client, get_kb_config, tiered_knowledge_collection
    from app.kb_pipeline_nvd import parse_nvd_cve_response
    from app.clients.trace_client import emit_trace
    from app.models import TraceEvent

    summary: dict[str, Any] = {"upserted": 0, "errors": 0, "skipped": False}
    if not force and not kb_pipeline_cve_enabled():
        summary["skipped"] = True
        summary["reason"] = "KB_PIPELINE_CVE_ENABLED off"
        return summary

    cfg = get_kb_config()
    if not cfg.enabled:
        summary["skipped"] = True
        summary["reason"] = "kb_disabled"
        return summary

    client = get_kb_client()
    if not isinstance(client, QdrantKBClient):
        summary["skipped"] = True
        summary["reason"] = "not_qdrant"
        return summary

    api_key = (os.getenv("KB_NVD_API_KEY") or "").strip()

    params: dict[str, str] = {
        "resultsPerPage": str(kb_cve_nvd_page_size()),
        "startIndex": "0",
        "lastModStartDate": _iso_last_mod_start(),
    }
    if api_key:
        params["apiKey"] = api_key

    try:
        async with httpx.AsyncClient(timeout=120.0) as hc:
            r = await hc.get(NVD_CVE_API, params=params)
            r.raise_for_status()
            doc = r.json()
    except Exception:
        logger.exception("kb_pipeline cve nvd fetch failed")
        summary["errors"] += 1
        return summary

    records = parse_nvd_cve_response(doc if isinstance(doc, dict) else None)
    collection = tiered_knowledge_collection(cfg, "cve")
    now = datetime.utcnow().isoformat() + "Z"
    max_u = kb_cve_max_upserts_per_run()
    upserted = 0

    for rec in records[:max_u]:
        cid = rec["cve_id"]
        point_id = client._stable_point_id(f"nvd|{cid}")
        snap = dict(rec.get("snapshot") or {})
        snap["source"] = "nvd"
        payload: dict[str, Any] = {
            "kind": "knowledge",
            "schema_version": "kb-know-v1",
            "kb_tier": "cve",
            "phase": "VULN_SCAN",
            "tool_id": "nvd_cve_pipeline",
            "source": "nvd",
            "tags": [cid],
            "title": cid,
            "summary": rec.get("summary") or cid,
            "source_url": f"https://nvd.nist.gov/vuln/detail/{cid}",
            "created_at": now,
            "updated_at": now,
            "context_snapshot": snap,
        }
        try:
            await client.upsert_pipeline_knowledge_vector(
                collection=collection,
                point_id=point_id,
                embed_text=str(rec.get("embed_text") or rec.get("summary") or cid),
                payload=payload,
            )
            upserted += 1
        except Exception:
            logger.exception("kb_pipeline cve upsert failed cve_id=%s", cid)
            summary["errors"] += 1

    summary["upserted"] = upserted
    try:
        await emit_trace(
            TraceEvent(
                task_id="kb-pipeline",
                timestamp=now,
                event_type="KB_PIPELINE_CVE_SYNC",
                source_module="orchestrator",
                payload={
                    "upserted": upserted,
                    "errors": summary["errors"],
                    "collection": collection,
                    "lookback_hours": kb_cve_nvd_lookback_hours(),
                },
            )
        )
    except Exception:
        pass
    return summary


async def run_blog_fetch_once(*, force: bool = False) -> dict[str, Any]:
    from app.clients.kb_client import QdrantKBClient, get_kb_client, get_kb_config, tiered_knowledge_collection
    from app.kb_blog_purify import purify_blog_plain_text, strip_html_boilerplate
    from app.clients.trace_client import emit_trace
    from app.models import TraceEvent

    summary: dict[str, Any] = {"upserted": 0, "errors": 0, "skipped": False, "urls": 0}
    if not force and not kb_pipeline_blog_enabled():
        summary["skipped"] = True
        summary["reason"] = "KB_PIPELINE_BLOG_ENABLED off"
        return summary

    urls = kb_blog_source_urls()
    if not urls:
        summary["skipped"] = True
        summary["reason"] = "KB_BLOG_SOURCE_URLS empty"
        return summary

    cfg = get_kb_config()
    if not cfg.enabled:
        summary["skipped"] = True
        summary["reason"] = "kb_disabled"
        return summary

    client = get_kb_client()
    if not isinstance(client, QdrantKBClient):
        summary["skipped"] = True
        summary["reason"] = "not_qdrant"
        return summary

    collection = tiered_knowledge_collection(cfg, "blogs")
    now = datetime.utcnow().isoformat() + "Z"
    max_b = kb_blog_max_response_bytes()
    summary["urls"] = len(urls)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as hc:
        for url in urls:
            try:
                r = await hc.get(url, headers={"User-Agent": "trustguard-agent-kb-pipeline/1.0"})
                r.raise_for_status()
                raw = r.content[:max_b]
                ctype = (r.headers.get("content-type") or "").lower()
                text_body: str
                if "html" in ctype or (raw[:100].strip().startswith(b"<")):
                    text_body = strip_html_boilerplate(raw.decode("utf-8", errors="replace"))
                else:
                    text_body = raw.decode("utf-8", errors="replace")
                plain = purify_blog_plain_text(text_body, max_chars=24000)
                if not plain.strip():
                    summary["errors"] += 1
                    continue
                title = url[:512]
                point_id = client._stable_point_id(f"blog|{url}")
                embed = plain[:8000]
                payload: dict[str, Any] = {
                    "kind": "knowledge",
                    "schema_version": "kb-know-v1",
                    "kb_tier": "blogs",
                    "phase": "THREAT_MODEL",
                    "tool_id": "blog_pipeline",
                    "source": "blog_fetch",
                    "tags": ["blog"],
                    "title": title,
                    "summary": plain[:12000],
                    "source_url": url[:4096],
                    "created_at": now,
                    "updated_at": now,
                    "context_snapshot": {"fetched_url": url[:2048]},
                }
                await client.upsert_pipeline_knowledge_vector(
                    collection=collection,
                    point_id=point_id,
                    embed_text=embed,
                    payload=payload,
                )
                summary["upserted"] += 1
            except Exception:
                logger.exception("kb_pipeline blog fetch failed url=%s", url)
                summary["errors"] += 1

    try:
        await emit_trace(
            TraceEvent(
                task_id="kb-pipeline",
                timestamp=now,
                event_type="KB_PIPELINE_BLOG_FETCH",
                source_module="orchestrator",
                payload={
                    "upserted": summary["upserted"],
                    "errors": summary["errors"],
                    "collection": collection,
                    "url_count": len(urls),
                },
            )
        )
    except Exception:
        pass
    return summary
