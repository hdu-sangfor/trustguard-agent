from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models import TaskState

_NOISE_PARAM_KEYS = {
    "boundary",
    "timestamp",
    "ts",
    "nonce",
    "trace_id",
    "request_id",
    "x_request_id",
    "x_trace_id",
    "user_agent",
    "ua",
}
# 动作签名中排除：易变 ID、落盘路径、执行器注入指针等（不参与「逻辑动作」区分）
_SIGNATURE_EXCLUDE_PARAM_KEYS = {
    "run_id",
    "request_id",
    "output_dir",
    "output_discovery_dir",
    "executor_artifact_ref",
    "executor_artifact_base",
    "todo_id",
    "agent_role",
}
_HTTP_SERVICE_MARKERS = ("http", "https", "web", "tomcat", "nginx", "apache", "iis")


def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8", errors="ignore")).hexdigest()


def _normalize_path(target: str) -> str:
    t = (target or "").strip()
    if not t:
        return "/"
    if "://" in t:
        p = urlparse(t)
        path = p.path or "/"
        return path if path.startswith("/") else f"/{path}"
    if "/" in t:
        path = t.split("/", 1)[-1]
        return f"/{path}" if not path.startswith("/") else path
    return "/"


def _extract_host_port(target: str) -> tuple[str, int | None, str]:
    t = (target or "").strip()
    if not t:
        return "", None, "http"
    if "://" not in t:
        t = f"http://{t}"
    p = urlparse(t)
    host = p.hostname or ""
    scheme = p.scheme or "http"
    port = p.port or (443 if scheme == "https" else 80)
    return host, port, scheme


def _compose_url(host: str, port: int, scheme: str) -> str:
    sch = "https" if str(scheme).lower() == "https" or int(port) == 443 else "http"
    return f"{sch}://{host}:{int(port)}"


def _pick_from_headers(headers: dict[str, Any], *candidates: str) -> str:
    low_map = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    for c in candidates:
        if c.lower() in low_map and low_map[c.lower()].strip():
            return low_map[c.lower()].strip()
    return ""


def _extract_endpoint_facts(target: str, artifacts: dict[str, Any]) -> dict[str, Any]:
    status = artifacts.get("status_code") or artifacts.get("status") or artifacts.get("http_status")
    try:
        status_int = int(status)
    except Exception:
        status_int = None
    body = str(
        artifacts.get("response_body")
        or artifacts.get("body")
        or artifacts.get("raw_preview")
        or artifacts.get("response_preview")
        or ""
    )
    headers = artifacts.get("response_headers") if isinstance(artifacts.get("response_headers"), dict) else {}
    title = str(artifacts.get("title") or "")
    content_hash = _sha256_text(body[:4000] + "|" + title)
    path = _normalize_path(target)
    notes = artifacts.get("notes") or artifacts.get("framework") or artifacts.get("stack_hint") or ""
    return {
        "path": path,
        "status": status_int,
        "content_hash": content_hash,
        "notes": str(notes)[:300],
        "server": _pick_from_headers(headers, "server"),
        "x_powered_by": _pick_from_headers(headers, "x-powered-by"),
    }


def _is_http_related(skill_id: str, target: str, artifacts: dict[str, Any]) -> bool:
    sid = (skill_id or "").lower()
    if any(x in sid for x in ("http", "curl", "web", "dirsearch", "katana", "nuclei", "ehole")):
        return True
    if "://" in (target or ""):
        return True
    text = json.dumps(artifacts or {}, ensure_ascii=False).lower()
    return "http" in text or "status_code" in text


def _looks_http_service(service_name: str) -> bool:
    s = (service_name or "").strip().lower()
    if not s:
        return False
    return any(m in s for m in _HTTP_SERVICE_MARKERS)


def _extract_framework_fingerprints(artifacts: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if isinstance(artifacts.get("fingerprints"), list):
        out.extend([str(x).strip() for x in artifacts.get("fingerprints") if str(x).strip()])
    for key in ("framework", "stack_hint", "whatweb", "server", "x_powered_by"):
        val = artifacts.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
    # stable de-dup
    uniq: list[str] = []
    seen: set[str] = set()
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        uniq.append(item)
    return uniq[:30]


def _stable_json(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(v)


def _sanitize_payload_text(s: str) -> str:
    text = (s or "").strip()
    # strip noisy boundary tokens/timestamps
    text = re.sub(r"----WebKitFormBoundary[A-Za-z0-9]+", "BOUNDARY", text)
    text = re.sub(r"\b\d{10,13}\b", "TS", text)
    return text


def memory_fact_max_chars() -> int:
    raw = (os.getenv("ORCH_MEMORY_FACT_MAX_CHARS") or "480").strip()
    try:
        return max(80, min(int(raw), 4000))
    except ValueError:
        return 480


def sanitize_llm_memory_remove_lines(items: list[str] | None) -> list[str]:
    """facts_to_remove：去掉过长行与明显工具 JSON，防止误删或注入。"""
    out: list[str] = []
    max_len = memory_fact_max_chars()
    for raw in items or []:
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s or len(s) > max_len:
            continue
        if s.startswith("{") and any(k in s for k in ('"params"', "'params'", '"skill_id"')):
            continue
        out.append(s)
    return out[:80]


def sanitize_llm_memory_fact_lines(facts: list[str] | None) -> list[str]:
    """
    过滤 LLM 写入 confirmed_facts / Plan metadata 的条目，避免把工具 params、整段 JSON、
    或 action_ledger 风格的行原样灌进「内部记忆」导致上下文污染与死循环复述。
    """
    out: list[str] = []
    if not facts:
        return out
    max_len = memory_fact_max_chars()
    for raw in facts:
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s:
            continue
        if len(s) > max_len:
            continue
        low = s.lower()
        # 像历史账本或工具调用轨迹的行，不应作为「事实」进入 Tier1
        if re.match(r"^\[[\w.-]+\]\s+target=", s):
            continue
        if "action_signature=" in low or "canonical_params=" in low:
            continue
        if "request_id=" in low and "sig=" in low:
            continue
        # 典型「把工具 JSON 当一条事实」的污染
        if s.startswith("{") and any(k in s for k in ('"params"', "'params'", '"skill_id"', "'skill_id'")):
            continue
        if s.startswith("[") and s.endswith("}") and len(s) > 200:
            continue
        out.append(s)
    return out


def sanitize_plan_list_metadata(plan_list: Any) -> Any:
    """
    持久化 PlanList 前清洗各 item.metadata 中的 facts_*，避免工具载荷进入 checkpoint。
    """
    from app.plan_models import PlanList

    if not isinstance(plan_list, PlanList):
        return plan_list
    new_items: list[Any] = []
    for it in plan_list.items or []:
        md = dict(it.metadata or {})
        for key in ("facts_to_add", "updated_facts"):
            v = md.get(key)
            if isinstance(v, list):
                flat = [str(x) if not isinstance(x, str) else x for x in v]
                md[key] = sanitize_llm_memory_fact_lines(flat)
        fr = md.get("facts_to_remove")
        if isinstance(fr, list):
            flat_rm = [str(x) if not isinstance(x, str) else x for x in fr]
            md["facts_to_remove"] = sanitize_llm_memory_remove_lines(flat_rm)
        new_items.append(it.model_copy(update={"metadata": md}, deep=True))
    return plan_list.model_copy(update={"items": new_items}, deep=True)


def redact_canonical_params_for_llm(cp: dict[str, Any] | None) -> dict[str, Any]:
    """
    Tier2 决策上下文：仅向 LLM 暴露低风险标量键，避免 headers/cookie/完整 cmd 进入 prompt。
    """
    if not isinstance(cp, dict) or not cp:
        return {}
    allow = frozenset(
        {
            "url",
            "target",
            "host",
            "ports",
            "port",
            "path",
            "method",
            "scheme",
            "timeout",
            "threads",
            "rate_limit",
            "rl",
            "max_depth",
            "depth",
        }
    )
    out: dict[str, Any] = {}
    for k, v in cp.items():
        key = str(k).strip().lower()
        if key not in allow:
            continue
        if isinstance(v, str):
            out[key] = v[:240]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[key] = v
        else:
            out[key] = _sanitize_payload_text(_stable_json(v))[:320]
    return dict(sorted(out.items()))


def build_canonical_params_subset(params: dict[str, Any]) -> dict[str, Any]:
    raw = dict(params or {})
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key in _NOISE_PARAM_KEYS or key in _SIGNATURE_EXCLUDE_PARAM_KEYS:
            continue
        if len(key) > 64:
            continue
        if key == "headers" and isinstance(v, dict):
            h_out = {}
            for hk, hv in v.items():
                hk_low = str(hk).strip().lower().replace("-", "_")
                if hk_low in _NOISE_PARAM_KEYS or hk_low in ("date",):
                    continue
                h_out[hk_low] = str(hv)[:200]
            out[key] = dict(sorted(h_out.items()))
            continue
        if isinstance(v, (dict, list)):
            out[key] = _sanitize_payload_text(_stable_json(v))[:500]
        elif isinstance(v, str):
            out[key] = _sanitize_payload_text(v)[:500]
        else:
            out[key] = v
    return dict(sorted(out.items()))


def build_action_signature(skill_id: str, target: str, params: dict[str, Any]) -> str:
    host, port, _scheme = _extract_host_port(target)
    canonical = build_canonical_params_subset(params or {})
    material = {
        "skill_id": (skill_id or "").strip().lower(),
        "host": host.lower(),
        "port": int(port or 0),
        "params": canonical,
    }
    return _sha256_text(_stable_json(material))


def append_action_ledger(
    state: TaskState,
    *,
    skill_id: str,
    target: str,
    params: dict[str, Any],
    exec_status: str,
    resolved_artifacts: dict[str, Any],
) -> dict[str, Any]:
    canonical = build_canonical_params_subset(params or {})
    signature = build_action_signature(skill_id, target, params or {})
    endpoint = _extract_endpoint_facts(target, resolved_artifacts or {}) if _is_http_related(skill_id, target, resolved_artifacts or {}) else {}
    row = {
        "ts": int(time.time()),
        "skill_id": skill_id,
        "target": target,
        "exec_status": exec_status,
        "canonical_params": canonical,
        "action_signature": signature,
        "endpoint_facts": endpoint,
        "artifact_ref": str((resolved_artifacts or {}).get("_artifact_ref") or ""),
    }
    state.action_ledger.append(row)
    return row


def update_entity_blackboard(
    state: TaskState,
    *,
    skill_id: str,
    target: str,
    resolved_artifacts: dict[str, Any],
) -> dict[str, Any]:
    board = state.entity_blackboard or {"targets": {}}
    targets = board.setdefault("targets", {})
    host, port, scheme = _extract_host_port(target)
    if not host:
        state.entity_blackboard = board
        return board
    key = f"{scheme}://{host}:{port or (443 if scheme == 'https' else 80)}"
    node = targets.setdefault(
        key,
        {
            "service_type": "http" if _is_http_related(skill_id, target, resolved_artifacts or {}) else "unknown",
            "framework_fingerprints": [],
            "endpoints": {},
            "ports": [port] if port else [],
        },
    )
    if port and port not in node.get("ports", []):
        node.setdefault("ports", []).append(port)
    fps = _extract_framework_fingerprints(resolved_artifacts or {})
    if fps:
        old = node.setdefault("framework_fingerprints", [])
        seen = set(old)
        for fp in fps:
            if fp not in seen:
                old.append(fp)
                seen.add(fp)
    if _is_http_related(skill_id, target, resolved_artifacts or {}):
        endpoint = _extract_endpoint_facts(target, resolved_artifacts or {})
        path = endpoint.get("path") or "/"
        node.setdefault("endpoints", {})[path] = endpoint
    state.entity_blackboard = board
    return board


def add_tier1_fact(state: TaskState, fact: str, *, max_facts: int = 50) -> None:
    """
    高优先级事实写入助手：
    - 使用简单子串匹配做幂等控制（同一 URL/模板不重复刷屏）
    - 将 confirmed_facts 约束在最近 max_facts 条，以防 Tier1 失控膨胀
    """
    s = fact.strip() if isinstance(fact, str) else ""
    if not s:
        return
    existing = [x for x in (state.confirmed_facts or []) if isinstance(x, str) and x.strip()]
    # 若任何已有事实已包含当前 fact 的关键信息，则视为已记录
    for line in existing:
        if s in line or line in s:
            return
    existing.append(s)
    state.confirmed_facts = existing[-max_facts:]


def apply_fact_updates(state: TaskState, facts_to_add: list[str], facts_to_remove: list[str]) -> tuple[list[str], list[str]]:
    facts_to_add = sanitize_llm_memory_fact_lines(list(facts_to_add or []))
    facts_to_remove = sanitize_llm_memory_remove_lines(list(facts_to_remove or []))
    existing = [x for x in (state.confirmed_facts or []) if isinstance(x, str) and x.strip()]
    removed: list[str] = []
    if facts_to_remove:
        rm_set = {x.strip() for x in facts_to_remove if isinstance(x, str) and x.strip()}
        kept = []
        for fact in existing:
            if fact in rm_set:
                removed.append(fact)
            else:
                kept.append(fact)
        existing = kept
    added: list[str] = []
    seen = set(existing)
    for fact in (facts_to_add or []):
        s = fact.strip() if isinstance(fact, str) else ""
        if not s or s in seen:
            continue
        seen.add(s)
        existing.append(s)
        added.append(s)
    state.confirmed_facts = existing[-120:]
    return added, removed


def _blackboard_compact_view(state: TaskState) -> dict[str, Any]:
    out: dict[str, Any] = {"targets": {}}
    board = state.entity_blackboard or {}
    for tgt, node in (board.get("targets") or {}).items():
        if not isinstance(node, dict):
            continue
        eps = node.get("endpoints") if isinstance(node.get("endpoints"), dict) else {}
        out["targets"][tgt] = {
            "service_type": node.get("service_type"),
            "framework_fingerprints": (node.get("framework_fingerprints") or [])[:10],
            "ports": (node.get("ports") or [])[:10],
            "endpoints": {k: {"status": v.get("status"), "content_hash": v.get("content_hash")} for k, v in list(eps.items())[:20] if isinstance(v, dict)},
        }
    return out


def build_context_snapshot_for_put(state: TaskState) -> dict[str, Any]:
    snapshot = dict(state.target_context or {})
    snapshot["entity_blackboard"] = _blackboard_compact_view(state)
    snapshot["confirmed_facts"] = list(state.confirmed_facts or [])[-80:]
    snapshot["fallback_baseline"] = dict(state.fallback_baseline or {})
    snapshot["action_ledger_recent"] = list(state.action_ledger)[-8:]
    return snapshot


def detect_repeated_signature(state: TaskState, signature: str, lookback: int = 8) -> bool:
    if not signature:
        return False
    recent = list(state.action_ledger)[-max(1, lookback):]
    matched = [row for row in recent if isinstance(row, dict) and (row or {}).get("action_signature") == signature]
    if not matched:
        return False
    # 对失败场景放宽去重：允许至少一次“同签名重试”，避免在 EXPLOIT 阶段形成死循环拦截。
    if len(matched) <= 1:
        return False
    statuses = {str((row or {}).get("exec_status") or "").strip().upper() for row in matched}
    if statuses and statuses.issubset({"FAILED", "TIMEOUT", "ERROR"}) and len(matched) <= 2:
        return False
    return True


async def ensure_http_fallback_baseline(state: TaskState, target: str) -> dict[str, Any] | None:
    if os.getenv("MEMORY_V1_BASELINE_PROBE_ENABLED", "true").strip().lower() != "true":
        return None
    host, port, scheme = _extract_host_port(target)
    if not host or not port:
        return None
    slot = f"{host}:{port}"
    if slot in (state.fallback_baseline or {}):
        return state.fallback_baseline.get(slot)
    probe_path = f"/this_is_a_random_404_{random.randint(100000, 999999)}"
    url = f"{scheme}://{host}:{port}{probe_path}"
    status_code = 0
    content_hash = ""
    try:
        timeout = float(os.getenv("MEMORY_V1_BASELINE_TIMEOUT_SECONDS", "1.5"))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            status_code = int(resp.status_code)
            content_hash = _sha256_text((resp.text or "")[:4000])
    except Exception:
        status_code = 0
        content_hash = ""
    record = {
        "baseline_fallback_hash": content_hash,
        "baseline_status_code": status_code,
        "sample_path": probe_path,
    }
    state.fallback_baseline[slot] = record
    return record


def infer_http_probe_targets(
    *,
    task_target: str,
    skill_id: str,
    target: str,
    resolved_artifacts: dict[str, Any],
) -> list[str]:
    """
    事件驱动 Baseline 候选 URL 生成：
    - HTTP 相关技能的当前 target（低成本兜底）
    - nmap / enum 结果中明确为 http/https 的端口或服务
    """
    out: list[str] = []
    host, _port, scheme = _extract_host_port(target or task_target)
    if host and _is_http_related(skill_id, target, resolved_artifacts or {}):
        t_host, t_port, t_scheme = _extract_host_port(target)
        if t_host and t_port:
            out.append(_compose_url(t_host, t_port, t_scheme))

    # 常见 open_ports: [80,443] / ["8080"]
    open_ports = (resolved_artifacts or {}).get("open_ports")
    if isinstance(open_ports, list) and host:
        for p in open_ports[:20]:
            try:
                port = int(p)
            except Exception:
                continue
            if port in (80, 443, 8080, 8000, 8443):
                out.append(_compose_url(host, port, "https" if port in (443, 8443) else scheme))

    # services: [{"port":8080,"service":"http"}, ...]
    services = (resolved_artifacts or {}).get("services")
    if isinstance(services, list) and host:
        for item in services[:40]:
            if not isinstance(item, dict):
                continue
            try:
                port = int(item.get("port") or 0)
            except Exception:
                continue
            service_name = str(item.get("service") or item.get("name") or "")
            if port > 0 and _looks_http_service(service_name):
                out.append(_compose_url(host, port, "https" if "https" in service_name.lower() else scheme))

    # stable de-dup
    uniq: list[str] = []
    seen: set[str] = set()
    for u in out:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return uniq[:20]


def is_crawler_confirmed_url(target_context: dict[str, Any] | None, url: str) -> bool:
    """
    爬虫/聚类产物中的 URL：在泛解析（fallback）场景下仍视为真实端点，不套用根页 baseline 的「假路径」判定。
    """
    if not url or not isinstance(target_context, dict):
        return False
    raw = target_context.get("crawler_confirmed_url_set")
    if not isinstance(raw, (list, tuple, set)):
        return False
    u = str(url).strip()
    if not u:
        return False
    seq = list(raw)
    if u in seq:
        return True
    try:
        from app.core.http_enum_seeds import normalize_url_for_pipeline

        nu = normalize_url_for_pipeline(u)
        if nu and nu in seq:
            return True
    except Exception:
        pass
    return False


def classify_endpoint_with_baseline(state: TaskState, target: str, content_hash: str) -> dict[str, Any]:
    host, port, _scheme = _extract_host_port(target)
    slot = f"{host}:{port}" if host and port else ""
    baseline = (state.fallback_baseline or {}).get(slot) if slot else None
    if not baseline:
        return {"is_fallback_alias": False}
    hit = bool(content_hash and baseline.get("baseline_fallback_hash") and content_hash == baseline.get("baseline_fallback_hash"))
    return {
        "is_fallback_alias": hit,
        "baseline_slot": slot,
        "baseline_status_code": baseline.get("baseline_status_code"),
    }
