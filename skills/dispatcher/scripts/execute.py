"""
dispatcher: operation=prepare | finalize
prepare: ETL + template dedupe + chunking + manifest.json
finalize: merge results/chunk_*.jsonl → vulnerabilities + histogram
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent.parent
_COMMON = _ROOT / "common"
if _COMMON.is_dir() and str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))

from etl import (  # noqa: E402
    DETERMINISM_VERSION,
    chunk_urls,
    merge_and_refine,
    normalize_scope_hostname,
    pick_high_value_endpoints,
    scope_hosts_for_payload,
    suspicious_signals,
    template_dedupe_representatives,
)
from fingerprint_tags import (  # noqa: E402
    default_tags_for_stack,
    derive_nuclei_tags_override,
    derive_stack_hint,
    load_fingerprint_rules,
)
from katana_io import dirsearch_urls_from_json, katana_urls_from_file  # noqa: E402
from workspace_resolve import resolve_under_workspace  # noqa: E402
from nuclei_io import merge_histograms, merge_tech_stack_evidence, summarize_nuclei_jsonl  # noqa: E402
from workspace_manifest import (  # noqa: E402
    build_manifest,
    chunk_rel_path,
    chunks_dir,
    context_fingerprint,
    discovery_dir,
    load_manifest,
    manifest_path,
    result_rel_path,
    results_dir,
    run_root,
    safe_task_id,
    save_manifest,
    sha256_text,
)

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_FINGERPRINT_FILE = _CONFIG_DIR / "fingerprint_tag_map.json"


def _truthy(v: object) -> bool:
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
        return True
    return False


def _pick_target_url(params: dict[str, object], payload: dict[str, object]) -> str:
    for key in ("target_url", "url", "scan_url", "endpoint", "target"):
        v = params.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    t = payload.get("target")
    return str(t).strip() if t is not None else ""


def emit_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _resolve_rr(ws: str, task_id: str, run_id: str, executor_artifact_base: str) -> Path:
    rr = run_root(ws, task_id, run_id)
    if executor_artifact_base:
        try:
            p = Path(executor_artifact_base)
            if not p.is_absolute():
                p = Path(ws) / p
            if len(p.parts) >= 4:
                task_root = p.parents[2]
                rr = task_root / "web-vuln" / run_id
        except Exception:
            pass
    return rr


def _to_ws_rel(path: Path, ws: str) -> str:
    try:
        return path.resolve().relative_to(Path(ws).resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _build_context_block(params: dict[str, Any], context: dict[str, Any], rules: list) -> dict[str, Any]:
    auth_header = str(params.get("auth_header") or context.get("auth_header") or "").strip()
    user_agent = str(params.get("user_agent") or context.get("user_agent") or "").strip()
    fp_text = str(context.get("fingerprint") or context.get("whatweb") or "").lower()
    tags = params.get("tags") or params.get("nuclei_tags") or params.get("template_tags")
    tags_s = ""
    if isinstance(tags, str) and tags.strip():
        tags_s = tags.strip()
    elif isinstance(tags, list) and tags:
        tags_s = ",".join(str(x).strip() for x in tags if str(x).strip())
    if not tags_s:
        override = derive_nuclei_tags_override(fp_text, rules)
        if override:
            tags_s = override
    if not tags_s:
        hint = derive_stack_hint(params, context, rules)
        tags_s = default_tags_for_stack(hint)
    fp = context_fingerprint(auth_header, user_agent)
    return {
        "auth_header": auth_header,
        "user_agent": user_agent,
        "nuclei_tags": tags_s,
        "context_fingerprint": fp,
    }


def do_prepare(payload: dict[str, Any], start: float) -> dict[str, Any]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    target = _pick_target_url(params, payload)
    if not target:
        return {
            "status": "FAILED",
            "parsed_artifacts": {"error": "target required"},
            "raw_stdout": "",
            "raw_stderr": "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
    if not target.startswith(("http://", "https://")):
        target = f"http://{target}"

    task_id = safe_task_id(str(payload.get("task_id") or "local"))
    run_id = str(params.get("run_id") or "").strip()
    if not run_id:
        return {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.run_id required (from katana)"},
            "raw_stdout": "",
            "raw_stderr": "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }

    ws = os.environ.get("WORKSPACE_ROOT", "/tmp")
    pre_base = str(context.get("executor_artifact_base") or "").strip()
    rr = _resolve_rr(ws, task_id, run_id, pre_base)
    rr.mkdir(parents=True, exist_ok=True)

    rules = load_fingerprint_rules(_FINGERPRINT_FILE)

    # Target-Centric Shared Workspace：优先从固定目录读取 katana/dirsearch 产物
    discovery_input_dir_raw = params.get("output_discovery_dir") or params.get("output_dir")
    dd_input: Path | None = None
    if isinstance(discovery_input_dir_raw, str) and discovery_input_dir_raw.strip():
        try:
            raw_s = discovery_input_dir_raw.strip()
            dd_try = resolve_under_workspace(raw_s)
            if dd_try is not None and dd_try.is_dir():
                dd_input = dd_try
            else:
                legacy = Path(raw_s)
                dd_input = legacy if legacy.exists() else None
        except Exception:
            dd_input = None

    dd = dd_input or discovery_dir(rr)
    katana_path = dd / "katana_urls.txt"
    dirsearch_path = dd / "dirsearch.json"

    katana_urls = katana_urls_from_file(katana_path, fallback_base=target)
    dir_urls = dirsearch_urls_from_json(dirsearch_path) if dirsearch_path.exists() else []

    max_urls = int(params.get("max_urls") or 500)
    chunk_size = max(1, int(params.get("chunk_size") or 20))

    seed_urls: list[str] = []
    su = params.get("seed_urls")
    if isinstance(su, list):
        seed_urls = [str(x).strip() for x in su if str(x).strip()]
    elif isinstance(su, str) and su.strip():
        seed_urls = [x.strip() for x in su.splitlines() if x.strip()]
    ctx_fb = context.get("http_enum_fallback_urls")
    if isinstance(ctx_fb, list):
        for x in ctx_fb:
            s = str(x).strip()
            if s and s not in seed_urls:
                seed_urls.append(s)

    host_alias_map: dict[str, str] | None = None
    ham = params.get("host_alias_map")
    if isinstance(ham, dict):
        host_alias_map = {str(k).lower().strip(): str(v).strip() for k, v in ham.items() if str(k).strip()}

    # 有 allowed_target 时强制使用任务绑定主机集合，忽略 LLM/参数中的过宽 scope，防止第三方 CDN 域名进入 Nuclei。
    allow_scope_subdomains = _truthy(params.get("allow_scope_subdomains"))
    scope_hosts: set[str] | None = None
    if str(payload.get("allowed_target") or "").strip():
        scope_hosts = scope_hosts_for_payload(target, payload)
        allow_scope_subdomains = False
    else:
        sh = params.get("scope_hosts") or params.get("scope_allowlist")
        if isinstance(sh, list) and sh:
            scope_hosts = {
                normalize_scope_hostname(str(x).strip()) for x in sh if str(x).strip()
            }
        elif isinstance(sh, str) and sh.strip():
            scope_hosts = {
                normalize_scope_hostname(x.strip()) for x in sh.split(",") if x.strip()
            }
        else:
            try:
                host = normalize_scope_hostname(urlparse(target).hostname or "")
                if host:
                    scope_hosts = {host}
            except Exception:
                scope_hosts = None

    deny_extra: list[str] = []
    kdp = params.get("katana_deny_patterns")
    if isinstance(kdp, list):
        deny_extra = [str(x) for x in kdp if str(x).strip()]
    elif isinstance(kdp, str) and kdp.strip():
        deny_extra = [kdp.strip()]

    drop_unresolved = _truthy(params.get("drop_unresolved_hosts"))

    refined, stats = merge_and_refine(
        katana_urls,
        dir_urls,
        max_urls=max_urls,
        scope_hosts=scope_hosts,
        seed_urls=seed_urls or None,
        host_alias_map=host_alias_map,
        extra_deny_patterns=deny_extra or None,
        drop_unresolved_hosts=drop_unresolved,
        allow_scope_subdomains=allow_scope_subdomains,
    )

    reps, tmpl_dedup = template_dedupe_representatives(refined, host_alias_map)
    stats["template_deduped"] = tmpl_dedup

    if not reps:
        reps = [target]

    chunks = chunk_urls(reps, chunk_size)
    cd = chunks_dir(rr)
    rd = results_dir(rr)
    cd.mkdir(parents=True, exist_ok=True)
    rd.mkdir(parents=True, exist_ok=True)

    chunks_meta: list[dict[str, Any]] = []
    ttl = int(params.get("manifest_ttl_seconds") or 3600)
    now = int(time.time())

    for i, chunk in enumerate(chunks, start=1):
        rel = chunk_rel_path(i)
        abs_c = rr / rel.replace("/", os.sep)
        abs_c.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(chunk) + ("\n" if chunk else "")
        abs_c.write_text(body, encoding="utf-8")
        h = sha256_text(body)
        res_rel = result_rel_path(i)
        chunks_meta.append(
            {
                "index": i,
                "path": rel,
                "sha256": h,
                "status": "pending",
                "deadline_epoch": now + ttl,
                "result_path": res_rel,
            }
        )

    ctx_block = _build_context_block(params, context, rules)
    manifest = build_manifest(
        task_id=task_id,
        run_id=run_id,
        chunk_size=chunk_size,
        total_chunks=len(chunks),
        chunks_meta=chunks_meta,
        context_block=ctx_block,
        discovery_rel="discovery",
        determinism_version=DETERMINISM_VERSION,
        ttl_seconds=ttl,
    )
    save_manifest(rr, manifest)

    pre_wsref = str(context.get("executor_artifact_ref") or "").strip()
    high_val = pick_high_value_endpoints(reps, limit=10)
    susp = suspicious_signals(reps, limit=15)

    return {
        "status": "SUCCESS",
        "parsed_artifacts": {
            "operation": "prepare",
            "run_id": run_id,
            "task_id": task_id,
            "target_url": target,
            "determinism_version": DETERMINISM_VERSION,
            "total_chunks": len(chunks),
            "chunk_size": chunk_size,
            "etl_stats": stats,
            "context_fingerprint": ctx_block.get("context_fingerprint"),
            "nuclei_tags": ctx_block.get("nuclei_tags"),
            "high_value_endpoints": high_val,
            "suspicious_signals": susp,
            "run_root": _to_ws_rel(rr, ws),
            "manifest_path": _to_ws_rel(manifest_path(rr), ws),
            "workspace_artifact_ref": pre_wsref or _to_ws_rel(rr, ws),
            "diagnostics": {"work_dir": _to_ws_rel(rr, ws)},
        },
        "raw_stdout": "",
        "raw_stderr": "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }


def do_finalize(payload: dict[str, Any], start: float) -> dict[str, Any]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    task_id = safe_task_id(str(payload.get("task_id") or "local"))
    run_id = str(params.get("run_id") or "").strip()
    if not run_id:
        return {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.run_id required"},
            "raw_stdout": "",
            "raw_stderr": "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }

    ws = os.environ.get("WORKSPACE_ROOT", "/tmp")
    pre_base = str(context.get("executor_artifact_base") or "").strip()
    rr = _resolve_rr(ws, task_id, run_id, pre_base)

    m = load_manifest(rr)
    if not m:
        return {
            "status": "FAILED",
            "parsed_artifacts": {"error": f"manifest not found under {rr}"},
            "raw_stdout": "",
            "raw_stderr": "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }

    all_vulns: list[dict[str, Any]] = []
    all_tech: list[dict[str, Any]] = []
    hist: dict[str, int] = {}
    low_info_total = 0
    partial = False
    pending: list[int] = []

    for ch in m.get("chunks") or []:
        if not isinstance(ch, dict):
            continue
        idx = int(ch.get("index") or 0)
        st = str(ch.get("status") or "").lower()
        if st != "done":
            pending.append(idx)
            partial = True
            continue
        rp = ch.get("result_path")
        if not isinstance(rp, str) or not rp.strip():
            partial = True
            continue
        p = rr / rp.replace("/", os.sep)
        v, h, li, tech = summarize_nuclei_jsonl(p)
        all_vulns.extend(v)
        hist = merge_histograms(hist, h)
        low_info_total += li
        all_tech = merge_tech_stack_evidence(all_tech, tech)

    pre_wsref = str(context.get("executor_artifact_ref") or "").strip()

    return {
        "status": "SUCCESS" if not pending else "SUCCESS",
        "parsed_artifacts": {
            "operation": "finalize",
            "run_id": run_id,
            "task_id": task_id,
            "partial_results": partial,
            "pending_chunk_indices": pending,
            "determinism_version": m.get("determinism_version") or DETERMINISM_VERSION,
            "severity_histogram": hist,
            "low_info_count": low_info_total,
            "vulnerabilities": all_vulns[:500],
            "tech_stack_evidence": all_tech[:300],
            "workspace_artifact_ref": pre_wsref or _to_ws_rel(rr, ws),
            "diagnostics": {"work_dir": _to_ws_rel(rr, ws), "finalize_pending_chunks": pending},
        },
        "raw_stdout": "",
        "raw_stderr": "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }


def main() -> int:
    start = time.perf_counter()
    raw_argv = sys.argv[1] if len(sys.argv) > 1 and str(sys.argv[1]).strip() else "{}"
    try:
        payload = json.loads(raw_argv)
    except Exception:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "invalid JSON argv"},
                "raw_stdout": "",
                "raw_stderr": "invalid json",
                "duration_ms": 0,
            }
        )
        return 1

    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    op = str(params.get("operation") or "prepare").lower().strip()
    if op == "finalize":
        out = do_finalize(payload, start)
    else:
        out = do_prepare(payload, start)
    emit_result(out)
    return 0 if out.get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
