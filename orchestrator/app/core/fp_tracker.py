"""
误报（False Positive）追踪模块。

职责：
- 扫描 confirmed_facts 中的 [Scanner_Unverified] / [Verified_False_Positive] 标记
- 维护结构化的 FPRecord 列表在 state.target_context["_fp_records"] 中
- 提供查询接口（get_fp_summary）供 API / 报告使用

设计原则：
- FP 追踪失败不应影响主流程（所有异常静默吞噬）
- _fp_records 使用下划线前缀，不会暴露给 LLM 决策上下文
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.models import TaskState

logger = logging.getLogger(__name__)

# ── 数据模型 ──────────────────────────────────────────────

@dataclass
class FPRecord:
    """单条 FP 判定记录，追踪从扫描发现到最终确认的完整生命周期。"""
    fp_id: str                              # "fp-{uuid[:12]}"
    task_id: str
    finding_signature: str                  # SHA256(template_id + "|" + url)
    template_id: str
    url: str
    title: str = ""
    severity: str = "info"
    source_skill_id: str = ""
    source_phase: str = ""
    current_verdict: str = "UNVERIFIED"     # UNVERIFIED | SUSPICIOUS | FALSE_POSITIVE | TRUE_POSITIVE | INCONCLUSIVE
    verification_source: str = ""           # LLM | HEURISTIC | HUMAN
    verification_reasoning: str = ""
    detected_at: str = ""
    verified_at: str = ""


# ── 签名工具 ──────────────────────────────────────────────

def make_finding_signature(template_id: str, url: str) -> str:
    """为 (template_id, url) 生成去重签名。"""
    raw = f"{_normalize(template_id)}|{_normalize(url)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


# ── Fact 解析 ─────────────────────────────────────────────

def _parse_scanner_unverified(fact: str) -> dict[str, Any] | None:
    """
    解析 "[Scanner_Unverified] {tid} detected on: {url1}, {url2}" 格式的 fact。
    返回 {"template_id": ..., "urls": [...]} 或 None。
    """
    s = fact.strip()
    if not s.startswith("[Scanner_Unverified]"):
        return None
    body = s[len("[Scanner_Unverified]"):].strip()
    if " detected on: " not in body:
        return None
    tid_part, urls_part = body.split(" detected on: ", 1)
    tid = tid_part.strip()
    if not tid:
        return None
    urls = [u.strip() for u in urls_part.split(",") if u.strip()]
    return {"template_id": tid, "urls": urls}


def _parse_verified_false_positive(fact: str) -> str | None:
    """
    解析 "[Verified_False_Positive] {reasoning}" 格式的 fact。
    返回 reasoning 文本；若不是 FP fact 则返回 None。
    """
    s = fact.strip()
    if s.startswith("[Verified_False_Positive]"):
        return s[len("[Verified_False_Positive]"):].strip()
    return None


# ── FPRecord 管理 ──────────────────────────────────────────

def _load_records(state: TaskState) -> list[dict[str, Any]]:
    """从 TaskState 加载已存在的 FP records。"""
    ctx = state.target_context or {}
    records = ctx.get("_fp_records")
    if not isinstance(records, list):
        records = []
        ctx["_fp_records"] = records
    return records


def ensure_fp_record(
    state: TaskState,
    *,
    template_id: str,
    url: str,
    severity: str = "info",
    source_skill_id: str = "",
    source_phase: str = "",
    title: str = "",
) -> str | None:
    """
    为 (template_id, url) 创建 UNVERIFIED 状态的 FPRecord（幂等）。
    返回 fp_id；已存在时返回已有 fp_id。
    """
    sig = make_finding_signature(template_id, url)
    records = _load_records(state)

    for r in records:
        if r.get("finding_signature") == sig:
            return r.get("fp_id")

    fp_id = "fp-" + uuid.uuid4().hex[:12]
    from datetime import datetime, timezone
    record = {
        "fp_id": fp_id,
        "task_id": state.task_id,
        "finding_signature": sig,
        "template_id": template_id,
        "url": url,
        "title": title or f"{template_id} on {url}",
        "severity": severity,
        "source_skill_id": source_skill_id,
        "source_phase": source_phase,
        "current_verdict": "UNVERIFIED",
        "verification_source": "",
        "verification_reasoning": "",
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "verified_at": "",
    }
    records.append(record)
    logger.debug("fp_tracker: registered %s template=%s url=%s", fp_id, template_id, url)
    _do_upsert(record)
    return fp_id


def scan_facts_for_fp(state: TaskState) -> list[dict[str, Any]]:
    """
    扫描 confirmed_facts，匹配 [Scanner_Unverified] → [Verified_False_Positive] 的
    验证流程，更新 FPRecord 的 verdict。

    返回变更过的 FPRecord 列表（用于 trace event 发射）。
    """
    facts = [str(f).strip() for f in (state.confirmed_facts or []) if isinstance(f, str) and f.strip()]
    records = _load_records(state)
    changed: list[dict[str, Any]] = []

    for fact in facts:
        # 检查是否为 Scanner_Unverified
        su = _parse_scanner_unverified(fact)
        if su:
            for url in su["urls"]:
                fp_id = ensure_fp_record(
                    state,
                    template_id=su["template_id"],
                    url=url,
                    severity="high",
                    source_skill_id="nuclei",
                    source_phase=state.current_phase.value if state.current_phase else "",
                )

        # 检查是否为 Verified_False_Positive
        reasoning = _parse_verified_false_positive(fact)
        if reasoning is not None:
            # 尝试匹配已有 FPRecord：从 reasoning 提取 URL 关键词
            matched = _match_fp_records_by_fact(records, fact)
            for r in records:
                if r.get("current_verdict") == "UNVERIFIED" and r in matched:
                    from datetime import datetime, timezone
                    r["current_verdict"] = "FALSE_POSITIVE"
                    r["verification_source"] = "LLM"
                    r["verification_reasoning"] = reasoning
                    r["verified_at"] = datetime.now(timezone.utc).isoformat()
                    changed.append(dict(r))
                    _do_upsert(dict(r))
                    logger.info("fp_tracker: %s → FALSE_POSITIVE (LLM) template=%s url=%s",
                                r["fp_id"], r.get("template_id"), r.get("url"))

    return changed


def _match_fp_records_by_fact(records: list[dict[str, Any]], fact: str) -> list[dict[str, Any]]:
    """
    在 FP records 中查找可能与此 fact 关联的记录。
    使用 URL 子串匹配 — 因为 fact 文本通常包含 endpoint URL。
    """
    matched: list[dict[str, Any]] = []
    fact_lower = fact.lower()
    for r in records:
        url = (r.get("url") or "").lower()
        if url and url in fact_lower:
            matched.append(r)
    if not matched:
        # fallback：取所有 UNVERIFIED 记录（无法精确匹配时的保守策略）
        matched = [r for r in records if r.get("current_verdict") == "UNVERIFIED"]
    return matched


# ── 查询接口 ──────────────────────────────────────────────

def get_fp_summary(state: TaskState) -> dict[str, Any]:
    """返回当前任务的 FP 判定汇总。"""
    records = _load_records(state)
    total = len(records)
    verdicts = {"UNVERIFIED": 0, "SUSPICIOUS": 0, "FALSE_POSITIVE": 0, "TRUE_POSITIVE": 0, "INCONCLUSIVE": 0}
    for r in records:
        v = r.get("current_verdict", "UNVERIFIED")
        if v in verdicts:
            verdicts[v] += 1

    fp_count = verdicts["FALSE_POSITIVE"]
    tp_count = verdicts["TRUE_POSITIVE"]
    resolved = fp_count + tp_count
    fp_rate = (fp_count / resolved) if resolved > 0 else 0.0

    return {
        "taskId": state.task_id,
        "total": total,
        "unverified": verdicts["UNVERIFIED"],
        "suspicious": verdicts["SUSPICIOUS"],
        "falsePositives": fp_count,
        "truePositives": tp_count,
        "inconclusive": verdicts["INCONCLUSIVE"],
        "falsePositiveRate": round(fp_rate, 4),
        "findings": [
            {
                "fpId": r.get("fp_id"),
                "taskId": r.get("task_id"),
                "templateId": r.get("template_id"),
                "url": r.get("url"),
                "title": r.get("title"),
                "severity": r.get("severity"),
                "sourceSkillId": r.get("source_skill_id"),
                "sourcePhase": r.get("source_phase"),
                "currentVerdict": r.get("current_verdict"),
                "verificationSource": r.get("verification_source") or None,
                "verificationReasoning": r.get("verification_reasoning") or None,
                "detectedAt": r.get("detected_at"),
                "verifiedAt": r.get("verified_at") or None,
            }
            for r in records
        ],
    }


# ── Trace Event 发射 ─────────────────────────────────────

async def emit_finding_registered_events(
    state: TaskState,
    template_id: str,
    urls: list[str],
) -> None:
    """在 Scanner_Unverified 创建时直接发射 FP_FINDING_REGISTERED trace events。
    由 agent_tools.py 在扫描器结果入库时调用。"""
    from app.models import TraceEvent
    from datetime import datetime, timezone
    from app.clients.trace_client import emit_trace

    ts = datetime.now(timezone.utc).isoformat()
    for url in urls:
        try:
            await emit_trace(TraceEvent(
                task_id=state.task_id,
                timestamp=ts,
                event_type="FP_FINDING_REGISTERED",
                source_module="fp_tracker",
                payload={
                    "template_id": template_id,
                    "url": url,
                    "severity": "high",
                    "source_phase": state.current_phase.value if state.current_phase else "",
                },
            ))
        except Exception:
            logger.debug("fp_tracker: trace emit failed for FP_FINDING_REGISTERED")

async def emit_fp_trace_events(state: TaskState, added_facts: list[str]) -> None:
    """
    为新添加的 FP 相关事实发射 trace event。
    在 state_machine._apply_decision_memory_updates() 中调用。
    """
    from app.models import TraceEvent
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()

    for fact in (added_facts or []):
        s = str(fact).strip() if isinstance(fact, str) else ""
        if not s:
            continue

        event_type = None
        payload: dict[str, Any] = {"fact": s}

        if s.startswith("[Scanner_Unverified]"):
            event_type = "FP_FINDING_REGISTERED"
            su = _parse_scanner_unverified(s)
            if su:
                payload["template_id"] = su["template_id"]
                payload["urls"] = su["urls"]
        elif s.startswith("[Verified_False_Positive]"):
            event_type = "FP_VERDICT_UPDATED"
            payload["verdict"] = "FALSE_POSITIVE"
            payload["source"] = "LLM"

        if event_type:
            try:
                from app.clients.trace_client import emit_trace
                await emit_trace(TraceEvent(
                    task_id=state.task_id,
                    timestamp=ts,
                    event_type=event_type,
                    source_module="fp_tracker",
                    payload=payload,
                ))
            except Exception:
                logger.debug("fp_tracker: trace emit failed for %s", event_type)


# ── DB 持久化辅助 ──────────────────────────────────────

def _do_upsert(record: dict[str, Any]) -> None:
    """将 FP 记录同步到 MySQL（异常静默吞噬）。"""
    try:
        from app.core.fp_persistence import upsert_fp_record
        upsert_fp_record(record)
    except Exception:
        logger.debug("fp_tracker: db upsert failed for %s", record.get("fp_id"))


def load_fp_from_db(task_id: str) -> list[dict[str, Any]]:
    """从 MySQL 加载指定任务的 FP 记录（用于 API fallback）。"""
    try:
        from app.core.fp_persistence import load_fp_records
        return load_fp_records(task_id)
    except Exception:
        return []


# ── T1: LLM 轻量判定 ────────────────────────────────────

async def call_fp_t1_mini_judge(state: TaskState) -> dict[str, Any] | None:
    """
    对当前所有 UNVERIFIED / SUSPICIOUS 的 FPRecord 做批量 LLM 轻量判定。
    在 VULN_SCAN 阶段扫描器执行完成后调用。
    返回 {fp_id: verdict} 或 None（未启用/无待判定记录/LLM 调用失败）。
    """
    if os.getenv("ORCH_FP_T1_ENABLED", "false").strip().lower() != "true":
        return None

    records = _load_records(state)
    pending = [r for r in records if r.get("current_verdict") in ("UNVERIFIED", "SUSPICIOUS")]
    if not pending:
        return None

    # 分批：每批最多 6 条，避免 LLM 输出被截断
    all_verdicts: list[dict[str, Any]] = []
    batch_size = 6
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start:batch_start + batch_size]
        batch_verdicts = await _call_t1_llm_batch(state, batch)
        if batch_verdicts:
            all_verdicts.extend(batch_verdicts)
        else:
            logger.warning("fp_tracker: T1 batch %d failed, skipping %d findings", batch_start // batch_size, len(batch))

    verdicts = all_verdicts

    # 更新 FPRecord
    from datetime import datetime, timezone
    updated: dict[str, str] = {}
    now = datetime.now(timezone.utc).isoformat()
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        fp_id = v.get("fp_id", "")
        verdict = v.get("verdict", "")
        reasoning = v.get("reasoning", "")
        if fp_id and verdict in ("TRUE_POSITIVE", "FALSE_POSITIVE", "INCONCLUSIVE"):
            for r in records:
                if r.get("fp_id") == fp_id:
                    old_v = r.get("current_verdict", "UNVERIFIED")
                    r["current_verdict"] = verdict
                    r["verification_source"] = "LLM"
                    r["verification_reasoning"] = reasoning
                    r["verified_at"] = now
                    updated[fp_id] = verdict
                    _do_upsert(dict(r))
                    # 发射 trace
                    try:
                        await emit_fp_verdict_trace(state, fp_id, old_v, verdict, "LLM")
                    except Exception:
                        pass
                    break

    logger.info("fp_tracker: T1 judged %d findings → %d updated", len(pending), len(updated))
    return updated


async def _call_t1_llm_batch(state: TaskState, batch: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """对一批 FPRecord 调用 LLM 判定。返回 parsed verdicts 或 None。"""
    ctx = state.target_context or {}
    board = ctx.get("entity_blackboard", {})
    framework = ""
    for tgt, node in (board.get("targets") or {}).items():
        if isinstance(node, dict):
            framework = ", ".join(node.get("framework_fingerprints", [])[:3])
            if framework:
                break

    items: list[str] = []
    for r in batch:
        cur = r.get("current_verdict", "UNVERIFIED")
        label = "SUSPICIOUS" if cur == "SUSPICIOUS" else "UNVERIFIED"
        items.append(f"[{r['fp_id']}] {r.get('template_id','')[:60]} → {r.get('url','')} (severity:{r.get('severity')}, {label})")

    # 查询 Qdrant FP KB 历史案例
    kb_context = ""
    for r in batch[:3]:  # 只查前3条的 KB，避免太慢
        try:
            hits = await query_fp_kb(r.get("template_id", ""), r.get("url", ""), limit=2)
            if hits:
                for h in hits[:1]:
                    p = h.get("payload") if isinstance(h, dict) else {}
                    if isinstance(p, dict) and p.get("verdict"):
                        kb_context += f"KB: {r.get('template_id','')[:40]} → previously {p['verdict']}\n"
        except Exception:
            pass

    system_msg = (
        "Classify each scanner finding as TRUE_POSITIVE, FALSE_POSITIVE, or INCONCLUSIVE. "
        "Respond with ONLY a JSON object, no markdown. Keep reasoning under 30 words each."
    )
    prefix = f"Historical FP knowledge:\n{kb_context}\n" if kb_context else ""
    user_msg = (
        f"Target: {state.target}\nFramework: {framework or 'unknown'}\n{prefix}\n"
        + "\n".join(items)
        + '\n\n{"verdicts":[{"fp_id":"...","verdict":"TRUE_POSITIVE|FALSE_POSITIVE|INCONCLUSIVE","reasoning":"short"}]}'
    )

    try:
        from app.clients.llm_client import _load_provider_config, _build_llm_headers, _post_chat_completions_json
        cfg = _load_provider_config()
        if not cfg.api_key:
            return None
        headers = _build_llm_headers(cfg)
        data = await _post_chat_completions_json(cfg, headers, {
            "model": cfg.model_id, "temperature": 0, "max_tokens": 2048, "stream": False,
            "messages": [{"role":"system","content":system_msg},{"role":"user","content":user_msg}],
        })
        if not data:
            return None
        choices = data.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            text = choices[0].get("text") or (choices[0].get("message") or {}).get("content") or ""
        if not text:
            return None

        import json as _json
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # JSON 修复：补全未闭合的括号和引号
        if not text.endswith("}"):
            # 尝试找到最后一个完整的 verdict 对象并闭合
            last_complete = text.rfind('"}')
            if last_complete > 0:
                text = text[:last_complete + 2] + "]}" if '"verdict"' in text[last_complete:] else text[:last_complete + 2] + "]}"
            else:
                text = text.rstrip(", \n\r\t") + "]}"

        result = _json.loads(text)
        verdicts = result.get("verdicts", []) if isinstance(result, dict) else []
        return [v for v in verdicts if isinstance(v, dict) and v.get("fp_id") and v.get("verdict") in ("TRUE_POSITIVE","FALSE_POSITIVE","INCONCLUSIVE")]
    except Exception as e:
        logger.warning("fp_tracker: T1 batch LLM failed: %s", e)
        return None


async def emit_fp_verdict_trace(state: TaskState, fp_id: str, old_v: str, new_v: str, source: str) -> None:
    """发射 FP_VERDICT_UPDATED trace event（T1/T2 共用）。"""
    try:
        from app.models import TraceEvent
        from app.clients.trace_client import emit_trace
        from datetime import datetime, timezone
        await emit_trace(TraceEvent(
            task_id=state.task_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="FP_VERDICT_UPDATED",
            source_module="fp_tracker",
            payload={"fp_id": fp_id, "old_verdict": old_v, "new_verdict": new_v, "source": source},
        ))
    except Exception:
        pass


# ── Qdrant FP 知识库 ─────────────────────────────────────

def _kb_enabled() -> bool:
    return os.getenv("KB_ENABLED", "false").strip().lower() == "true"


async def upsert_to_fp_kb(record: dict[str, Any]) -> None:
    """将已验证的 FP/TP 记录写入 Qdrant trustguard_fp_knowledge collection。"""
    if not _kb_enabled():
        return
    verdict = record.get("current_verdict", "")
    if verdict not in ("FALSE_POSITIVE", "TRUE_POSITIVE"):
        return

    try:
        from app.clients.kb_client import get_kb_client
        kb = get_kb_client()
        if kb is None:
            return

        collection = os.getenv("KB_COLLECTION_FP_KNOWLEDGE", "trustguard_fp_knowledge")
        embed_text = (
            f"{record.get('template_id', '')} | "
            f"{record.get('url', '')} | "
            f"{record.get('severity', '')} | "
            f"{verdict} | "
            f"{record.get('verification_reasoning', '')}"
        )[:2000]

        import hashlib
        point_id = hashlib.sha256(
            f"fp_kb:{record.get('fp_id', '')}".encode("utf-8")
        ).hexdigest()[:24]

        await kb._upsert_points(
            collection_name=collection,
            points=[{
                "id": point_id,
                "text": embed_text,
                "payload": {
                    "kind": "fp_knowledge",
                    "schema_version": "fp-v1",
                    "fp_id": record.get("fp_id"),
                    "task_id": record.get("task_id"),
                    "template_id": record.get("template_id"),
                    "url": record.get("url"),
                    "severity": record.get("severity"),
                    "verdict": verdict,
                    "verification_source": record.get("verification_source"),
                    "verification_reasoning": record.get("verification_reasoning"),
                    "created_at": record.get("verified_at") or record.get("detected_at"),
                },
            }],
        )
    except Exception:
        logger.debug("fp_tracker: Qdrant upsert failed for %s", record.get("fp_id"))


async def query_fp_kb(template_id: str, url: str, limit: int = 5) -> list[dict[str, Any]]:
    """查询 FP KB 中与当前 finding 相似的历史案例。"""
    if not _kb_enabled():
        return []
    try:
        from app.clients.kb_client import get_kb_client
        kb = get_kb_client()
        if kb is None:
            return []
        collection = os.getenv("KB_COLLECTION_FP_KNOWLEDGE", "trustguard_fp_knowledge")
        query_text = f"{template_id} | {url}"
        hits = await kb._search_collection(collection, query_text, limit=limit)
        return hits if isinstance(hits, list) else []
    except Exception:
        return []


# ── T2: 深度离线审计 ─────────────────────────────────────

async def call_fp_t2_deep_audit(state: TaskState) -> dict[str, Any]:
    """
    对 INCONCLUSIVE 的 FPRecord 做全量上下文深度审计。
    任务完成后通过 API 触发。
    """
    records = _load_records(state)
    inconclusive = [r for r in records if r.get("current_verdict") == "INCONCLUSIVE"]
    if not inconclusive:
        return {"audited": 0, "message": "no INCONCLUSIVE records"}

    if os.getenv("ORCH_FP_T1_ENABLED", "false").strip().lower() != "true":
        return {"audited": 0, "message": "T1 not enabled, T2 requires ORCH_FP_T1_ENABLED=true"}

    # 查询 FP KB
    results: dict[str, str] = {}
    for r in inconclusive:
        try:
            kb_hits = await query_fp_kb(r.get("template_id", ""), r.get("url", ""), limit=3)
            r["_kb_hits"] = kb_hits
        except Exception:
            r["_kb_hits"] = []

    # 构建深度审计 prompt
    ctx = state.target_context or {}
    board = ctx.get("entity_blackboard", {})
    framework = ""
    for tgt, node in (board.get("targets") or {}).items():
        if isinstance(node, dict):
            framework = ", ".join(node.get("framework_fingerprints", [])[:3])
            if framework:
                break

    items: list[str] = []
    for r in inconclusive:
        kb_info = ""
        kb_hits = r.get("_kb_hits", [])
        if kb_hits:
            kb_info = f"  Similar past cases: {len(kb_hits)} found in FP knowledge base"
        items.append(
            f"- [{r['fp_id']}] {r.get('template_id')} → {r.get('url')} "
            f"(severity: {r.get('severity')}){kb_info}"
        )

    system_msg = (
        "You are a senior penetration testing auditor specializing in false-positive analysis. "
        "For each finding below, review ALL available evidence and classify as:\n"
        "- TRUE_POSITIVE: real vulnerability with concrete exploit evidence\n"
        "- FALSE_POSITIVE: scanner error, no actual vulnerability present\n"
        "- INCONCLUSIVE: still cannot determine even after deep review\n"
        "Base your decision on: response content analysis, framework consistency, "
        "fallback baseline comparison, scanner consensus, and historical FP patterns. "
        "Provide a detailed evidence chain in your reasoning. "
        "Respond with ONLY a JSON object."
    )
    user_msg = (
        f"Target: {state.target}\n"
        f"Framework: {framework or 'unknown'}\n"
        f"Fallback baseline: {'available' if ctx.get('fallback_baseline') else 'not established'}\n"
        f"History summary: {(state.history_summary or '')[-2000:]}\n\n"
        + "Findings requiring deep audit:\n" + "\n".join(items)
        + '\n\nRespond with:\n{"verdicts": [{"fp_id": "...", "verdict": "TRUE_POSITIVE|FALSE_POSITIVE|INCONCLUSIVE", "reasoning": "detailed evidence chain"}]}'
    )

    try:
        from app.clients.llm_client import _load_provider_config, _build_llm_headers, _post_chat_completions_json

        cfg = _load_provider_config()
        if not cfg.api_key:
            return {"audited": 0, "message": "no LLM API key configured"}

        headers = _build_llm_headers(cfg)
        payload = {
            "model": cfg.model_id,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0,
            "max_tokens": 2048,
            "stream": False,
        }
        data = await _post_chat_completions_json(cfg, headers, payload)
        if not data:
            return {"audited": 0, "message": "LLM call returned empty"}

        choices = data.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            text = choices[0].get("text") or (choices[0].get("message") or {}).get("content") or ""

        if not text:
            return {"audited": 0, "message": "LLM returned no content"}

        import json as _json
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        result = _json.loads(text)
        verdicts = result.get("verdicts", []) if isinstance(result, dict) else []

    except Exception as e:
        logger.warning("fp_tracker: T2 LLM call failed: %s", e)
        return {"audited": 0, "message": str(e)}

    # 更新 FPRecord
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        fp_id = v.get("fp_id", "")
        verdict = v.get("verdict", "")
        reasoning = v.get("reasoning", "")
        if fp_id and verdict in ("TRUE_POSITIVE", "FALSE_POSITIVE", "INCONCLUSIVE"):
            for r in records:
                if r.get("fp_id") == fp_id and r.get("current_verdict") == "INCONCLUSIVE":
                    old_v = r.get("current_verdict", "UNVERIFIED")
                    r["current_verdict"] = verdict
                    r["verification_source"] = "T2_LLM_DEEP"
                    r["verification_reasoning"] = reasoning
                    r["verified_at"] = now
                    results[fp_id] = verdict
                    _do_upsert(dict(r))
                    await upsert_to_fp_kb(dict(r))
                    try:
                        await emit_fp_verdict_trace(state, fp_id, old_v, verdict, "T2_LLM_DEEP")
                    except Exception:
                        pass
                    break

    logger.info("fp_tracker: T2 audited %d findings → %d resolved", len(inconclusive), len(results))
    return {"audited": len(inconclusive), "resolved": len(results), "verdicts": results}


async def call_fp_t2_deep_audit_db(task_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """对 DB 中的 INCONCLUSIVE 记录做深度审计（任务已结束，无内存 TaskState）。"""
    from app.clients.llm_client import _load_provider_config, _build_llm_headers, _post_chat_completions_json

    cfg = _load_provider_config()
    if not cfg.api_key:
        return {"audited": 0, "message": "no LLM API key configured"}

    items = [f"- [{r['fp_id']}] {r.get('template_id')} → {r.get('url')} ({r.get('severity')})" for r in records]

    system_msg = (
        "You are a senior penetration testing auditor specializing in false-positive analysis. "
        "For each finding below, review evidence and classify as TRUE_POSITIVE, FALSE_POSITIVE, or INCONCLUSIVE. "
        "Consider: scanner template reliability, URL pattern, framework context. "
        "Respond with ONLY a JSON object."
    )
    user_msg = (
        f"Task: {task_id}\n"
        f"Findings requiring deep audit ({len(records)}):\n"
        + "\n".join(items)
        + '\n\nRespond with:\n{"verdicts": [{"fp_id": "...", "verdict": "...", "reasoning": "..."}]}'
    )

    import json as _json
    text = ""
    try:
        headers = _build_llm_headers(cfg)
        payload = {"model": cfg.model_id, "messages": [
            {"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}
        ], "temperature": 0, "max_tokens": 1024, "stream": False}
        data = await _post_chat_completions_json(cfg, headers, payload)
        if data:
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                text = choices[0].get("text") or (choices[0].get("message") or {}).get("content") or ""
    except Exception as e:
        return {"audited": 0, "message": f"LLM call failed: {e}"}

    if not text:
        return {"audited": 0, "message": "LLM returned no content"}

    try:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        result = _json.loads(text)
        verdicts = result.get("verdicts", []) if isinstance(result, dict) else []
    except Exception:
        return {"audited": 0, "message": "failed to parse LLM response"}

    results: dict[str, str] = {}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        fp_id = v.get("fp_id", "")
        verdict = v.get("verdict", "")
        if fp_id and verdict in ("TRUE_POSITIVE", "FALSE_POSITIVE", "INCONCLUSIVE"):
            for r in records:
                if r.get("fp_id") == fp_id:
                    r["current_verdict"] = verdict
                    r["verification_source"] = "T2_LLM_DEEP"
                    r["verification_reasoning"] = v.get("reasoning", "")
                    from datetime import datetime, timezone
                    r["verified_at"] = datetime.now(timezone.utc).isoformat()
                    results[fp_id] = verdict
                    _do_upsert(dict(r))

    logger.info("fp_tracker: T2(DB) audited %d → %d resolved", len(records), len(results))
    return {"audited": len(records), "resolved": len(results), "verdicts": results}
