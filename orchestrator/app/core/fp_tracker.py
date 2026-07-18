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
    current_verdict: str = "UNVERIFIED"     # UNVERIFIED | FALSE_POSITIVE | TRUE_POSITIVE | INCONCLUSIVE
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
    verdicts = {"UNVERIFIED": 0, "FALSE_POSITIVE": 0, "TRUE_POSITIVE": 0, "INCONCLUSIVE": 0}
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
