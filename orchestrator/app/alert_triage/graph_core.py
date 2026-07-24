"""告警研判 LangGraph 工作流内核。

8 节点状态图:
  load_alert → collect_evidence → check_whitelist → query_rag
                                                         ↓
  persist_result ← validate_decision ← make_decision ←──┘
       ↓
  recommend_action → END
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

# from ..clients import xdr_client, rag_client
# from ..clients.trace_client import emit_trace
# 
# from ..models import TraceEvent

from .models import (
    AlertTriageResult,
    AlertVerdict,
    ExecutionLevel,
    RagCitation,
    RecommendedAction,
    TokenUsage,
    XdrEvidenceRef,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
    source_module: str = "alert_triage",
) -> None:
    try:
        from clients.trace_client import emit_trace
        from models import TraceEvent
        await emit_trace(
            TraceEvent(
                task_id=task_id,
                timestamp=_now_iso(),
                event_type=event_type,
                source_module=source_module,
                payload=payload,
            )
        )
    except Exception:
        logger.warning("trace emit failed event_type=%s task_id=%s", event_type, task_id)


# ── LangGraph 兼容层 ────────────────────────────────────────────────

try:
    from langgraph.graph import END, StateGraph
except ModuleNotFoundError:

    class _MiniCompiledGraph:
        def __init__(self, graph: Any) -> None:
            self.graph = graph

        async def ainvoke(
            self, state: TriageState, config: dict[str, Any] | None = None
        ) -> TriageState:
            node = self.graph.entry_point
            current = dict(state)
            limit = int((config or {}).get("recursion_limit") or 25)
            for _ in range(limit):
                if node == END:
                    return current
                fn = self.graph.nodes[node]
                if getattr(fn, "_is_async", False):
                    current = dict(await fn(current))
                else:
                    current = dict(fn(current))
                if node in self.graph.conditional_edges:
                    router, mapping = self.graph.conditional_edges[node]
                    target = router(current)
                    node = mapping.get(target, END)
                else:
                    node = self.graph.edges.get(node, END)
            raise RuntimeError("mini graph recursion limit reached")

    class StateGraph:
        def __init__(self, _state_type: Any) -> None:
            self.nodes: dict[str, Any] = {}
            self.edges: dict[str, str] = {}
            self.conditional_edges: dict[str, Any] = {}
            self.entry_point = ""

        def add_node(self, name: str, fn: Any) -> None:
            self.nodes[name] = fn

        def set_entry_point(self, name: str) -> None:
            self.entry_point = name

        def add_edge(self, src: str, dest: str) -> None:
            self.edges[src] = dest

        def add_conditional_edges(
            self, src: str, router: Any, mapping: dict[str, str]
        ) -> None:
            self.conditional_edges[src] = (router, mapping)

        def compile(self) -> _MiniCompiledGraph:
            return _MiniCompiledGraph(self)

    END = "__end__"


# ── State ───────────────────────────────────────────────────────────


class TriageState(TypedDict, total=False):
    task_id: str
    alert_uuid: str
    scenario_id: str | None
    enable_rag: bool
    caller_notes: str | None
    # 工作流中间态
    alert: dict[str, Any] | None
    alert_proof: dict[str, Any] | None
    assets: list[dict[str, Any]]
    related_incidents: list[dict[str, Any]]
    incident_proofs: list[dict[str, Any]]
    whitelist_matches: list[dict[str, Any]]
    rag_response: dict[str, Any] | None
    rag_degraded: bool
    # 研判结果
    raw_decision: dict[str, Any] | None
    validation_errors: list[str]
    result: dict[str, Any] | None
    # 控制
    status: str
    error: str | None
    warnings: list[str]
    missing_evidence: list[str]
    started_at: str
    finished_at: str
    current_node: str
    trace_events: list[dict[str, Any]]


def _append_trace(state: TriageState, event: dict[str, Any]) -> list[dict[str, Any]]:
    return [*state.get("trace_events", []), event]


# ── Node: load_alert ────────────────────────────────────────────────


async def load_alert(state: TriageState) -> TriageState:
    """获取告警基本信息。"""
    alert_uuid = state["alert_uuid"]
    task_id = state["task_id"]
    from clients import xdr_client
    state["current_node"] = "load_alert"

    await _emit(task_id, "AT_LOAD_ALERT_START", {"alert_uuid": alert_uuid})

    try:
        alert = await xdr_client.get_alert(alert_uuid)
        if not alert or not isinstance(alert, dict):
            msg = f"Alert not found: {alert_uuid}"
            await _emit(task_id, "AT_LOAD_ALERT_FAILED", {"error": msg})
            return {
                **state,
                "status": "FAILED",
                "error": msg,
                "trace_events": _append_trace(
                    state,
                    {"type": "load_alert.error", "error": msg},
                ),
            }

        state["alert"] = alert
        await _emit(
            task_id,
            "AT_LOAD_ALERT_COMPLETE",
            {
                "alert_uuid": alert_uuid,
                "alert_type": alert.get("alert_type") or alert.get("type"),
                "severity": alert.get("severity"),
            },
        )
        return {
            **state,
            "trace_events": _append_trace(
                state,
                {"type": "load_alert.complete", "alert_uuid": alert_uuid},
            ),
        }
    except xdr_client.XDRClientError as e:
        msg = f"XDR unavailable loading alert: {e}"
        logger.warning("load_alert failed task_id=%s: %s", task_id, e)
        await _emit(task_id, "AT_LOAD_ALERT_FAILED", {"error": msg})
        return {
            **state,
            "status": "FAILED",
            "error": msg,
            "trace_events": _append_trace(
                state,
                {"type": "load_alert.error", "error": msg},
            ),
        }
load_alert._is_async = True  # type: ignore[attr-defined]


# ── Node: collect_evidence ──────────────────────────────────────────


async def collect_evidence(state: TriageState) -> TriageState:
    """获取 proof、资产和关联事件。"""
    task_id = state["task_id"]
    alert_uuid = state["alert_uuid"]
    from clients import xdr_client
    state["current_node"] = "collect_evidence"

    await _emit(task_id, "AT_COLLECT_EVIDENCE_START", {"alert_uuid": alert_uuid})

    missing: list[str] = []
    state["assets"] = []
    state["related_incidents"] = []
    state["incident_proofs"] = []
    state["alert_proof"] = {}

    # 1) proof
    try:
        proof = await xdr_client.get_alert_proof(alert_uuid)
        state["alert_proof"] = proof if isinstance(proof, dict) else {}
        if not proof:
            missing.append("alert_proof")
            state["missing_evidence"].append("alert_proof")
    except xdr_client.XDRClientError as e:
        logger.warning("alert proof missing task_id=%s: %s", task_id, e)
        missing.append("alert_proof")
        state["missing_evidence"].append("alert_proof")

    # 2) assets
    alert = state.get("alert") or {}
    asset_params: dict[str, Any] = {}
    hostname = str(
        alert.get("hostname") or alert.get("device_name") or ""
    ).strip()
    src_ip = str(alert.get("source_ip") or "").strip()
    if hostname:
        asset_params["hostname"] = hostname
    if src_ip:
        asset_params["ip"] = src_ip

    if asset_params:
        try:
            assets = await xdr_client.get_assets(asset_params)
            state["assets"] = assets if isinstance(assets, list) else []
        except xdr_client.XDRClientError as e:
            logger.warning("assets query failed task_id=%s: %s", task_id, e)
            state["missing_evidence"].append("assets")
    else:
        state["missing_evidence"].append("assets")

    # 3) related incidents
    incident_params: dict[str, Any] = {}
    incident_id = str(alert.get("incident_id") or "").strip()
    if incident_id:
        incident_params["incident_id"] = incident_id
    else:
        incident_params["alert_uuid"] = alert_uuid

    try:
        incidents = await xdr_client.list_incidents(incident_params)
        state["related_incidents"] = incidents if isinstance(incidents, list) else []
        # 获取每个 incident 的 proof
        for inc in state["related_incidents"][:5]:  # 最多取 5 个
            iid = str(inc.get("uuid") or inc.get("id", ""))
            if not iid:
                continue
            try:
                iproof = await xdr_client.get_incident_proof(iid)
                if isinstance(iproof, dict):
                    state["incident_proofs"].append(iproof)
            except xdr_client.XDRClientError:
                state["missing_evidence"].append(f"incident_proof:{iid}")
    except xdr_client.XDRClientError as e:
        logger.warning("incidents query failed task_id=%s: %s", task_id, e)
        state["missing_evidence"].append("related_incidents")

    await _emit(
        task_id,
        "AT_COLLECT_EVIDENCE_COMPLETE",
        {
            "has_proof": bool(state["alert_proof"]),
            "asset_count": len(state["assets"]),
            "incident_count": len(state["related_incidents"]),
            "missing_evidence": state["missing_evidence"],
        },
    )
    return {
        **state,
        "trace_events": _append_trace(
            state,
            {
                "type": "collect_evidence.complete",
                "missing": missing,
            },
        ),
    }
collect_evidence._is_async = True  # type: ignore[attr-defined]


# ── Node: check_whitelist ───────────────────────────────────────────


async def check_whitelist(state: TriageState) -> TriageState:
    """查询匹配白名单和可信业务行为。"""
    task_id = state["task_id"]
    from clients import xdr_client
    state["current_node"] = "check_whitelist"
    state["whitelist_matches"] = []

    await _emit(task_id, "AT_CHECK_WHITELIST_START", {})

    alert = state.get("alert") or {}
    whitelist_params: dict[str, Any] = {
        "alert_uuid": state["alert_uuid"],
    }

    # 添加告警特征以提高匹配精度
    sig_id = str(alert.get("signature_id") or alert.get("rule_id") or "").strip()
    cmd = str(alert.get("command_line") or "").strip()
    proc = str(alert.get("process_name") or "").strip()

    if sig_id:
        whitelist_params["signature_id"] = sig_id
    if cmd:
        whitelist_params["command_line"] = cmd
    if proc:
        whitelist_params["process_name"] = proc

    try:
        matches = await xdr_client.match_whitelist(whitelist_params)
        state["whitelist_matches"] = matches if isinstance(matches, list) else []
    except xdr_client.XDRClientError as e:
        logger.warning("whitelist check failed task_id=%s: %s", task_id, e)
        # whitelist 匹配失败不阻塞流程
        state["warnings"].append("whitelist_check_unavailable")

    match_info = {
        "match_count": len(state["whitelist_matches"]),
        "match_ids": [
            str(m.get("id") or m.get("rule_id", "") or "")
            for m in state["whitelist_matches"][:10]
        ],
    }
    await _emit(task_id, "AT_CHECK_WHITELIST_COMPLETE", match_info)
    return {
        **state,
        "trace_events": _append_trace(
            state,
            {"type": "check_whitelist.complete", **match_info},
        ),
    }
check_whitelist._is_async = True  # type: ignore[attr-defined]


# ── Node: query_rag ─────────────────────────────────────────────────


async def query_rag(state: TriageState) -> TriageState:
    """根据告警信息查询 RAG 知识库。"""
    from clients import rag_client
    task_id = state["task_id"]
    state["current_node"] = "query_rag"
    state["rag_response"] = None
    state["rag_degraded"] = False

    if not state.get("enable_rag", True):
        state["rag_degraded"] = True
        state["warnings"].append("RAG_DISABLED_BY_REQUEST")
        await _emit(task_id, "AT_QUERY_RAG_SKIPPED", {"reason": "disabled_by_request"})
        return {
            **state,
            "trace_events": _append_trace(
                state,
                {"type": "query_rag.skipped", "reason": "disabled_by_request"},
            ),
        }

    await _emit(task_id, "AT_QUERY_RAG_START", {})

    alert = state.get("alert") or {}
    questions = rag_client.build_questions_from_alert(alert)

    try:
        response = await rag_client.query_rag(questions)
        if response.degraded:
            state["rag_degraded"] = True
            state["warnings"].append("RAG_DEGRADED")
            await _emit(
                task_id, "AT_QUERY_RAG_DEGRADED", {"questions": questions}
            )
        else:
            state["rag_response"] = {
                "answer": response.answer,
                "citations": response.citations,
            }
            await _emit(
                task_id,
                "AT_QUERY_RAG_COMPLETE",
                {
                    "question_count": len(questions),
                    "citation_count": len(response.citations),
                },
            )
    except Exception as e:
        state["rag_degraded"] = True
        state["warnings"].append("RAG_DEGRADED")
        await _emit(task_id, "AT_QUERY_RAG_FAILED", {"error": str(e)})

    return {
        **state,
        "trace_events": _append_trace(
            state,
            {
                "type": "query_rag.complete",
                "degraded": state["rag_degraded"],
            },
        ),
    }
query_rag._is_async = True  # type: ignore[attr-defined]


# ── Node: make_decision ─────────────────────────────────────────────


async def make_decision(state: TriageState) -> TriageState:
    """基于 XDR 证据和 RAG 知识调用 LLM 生成结构化研判结论。"""
    task_id = state["task_id"]
    state["current_node"] = "make_decision"
    state["raw_decision"] = None

    await _emit(task_id, "AT_MAKE_DECISION_START", {})

    # 构建 LLM prompt
    alert = state.get("alert") or {}
    alert_proof = state.get("alert_proof") or {}
    whitelist_matches = state.get("whitelist_matches") or []
    rag_response = state.get("rag_response") or {}
    warnings = state.get("warnings", [])
    missing = state.get("missing_evidence", [])

    alert_type = str(alert.get("alert_type") or alert.get("type") or "unknown")
    alert_severity = str(alert.get("severity") or "medium")
    alert_name = str(alert.get("name") or alert.get("title") or alert_type)

    # 构建证据摘要
    proof_summary = _summarize_proof(alert_proof)
    whitelist_summary = ", ".join(
        str(m.get("id") or m.get("rule_id") or m.get("name", "") or "")
        for m in whitelist_matches[:5]
    ) or "无匹配"
    rag_summary = (
        str(rag_response.get("answer", "") or "")[:500]
        if rag_response and not state.get("rag_degraded")
        else "RAG 不可用"
    )
    degraded_note = (
        "[降级模式] RAG 知识不可用，仅根据 XDR 原始证据研判。" if state.get("rag_degraded") else ""
    )
    insufficient_note = (
        "[证据不足] 以下证据缺失: " + ", ".join(missing)
        if missing
        else ""
    )

    # 构建 system + user prompt
    system_prompt = """你是一个安全运营中心（SOC）的告警研判专家。你必须根据提供的 XDR 原始证据进行客观研判。

输出要求：
1. 输出必须是合法的 JSON 对象。
2. 结论字段 verdict 只能为: true_positive, false_positive, suspicious, insufficient_evidence
3. 证据明显不足时，必须输出 insufficient_evidence，且 confidence <= 0.5
4. severity 只能为: critical, high, medium, low, info
5. confidence 为 0.0-1.0 的浮点数
6. 建议动作 execution_level 只能为: manual_confirm, forbidden (MVP 不产生 auto 建议)
7. 必须列出证据引用和推理过程"""

    user_prompt = f"""请研判以下告警：

告警名称: {alert_name}
告警类型: {alert_type}
严重程度: {alert_severity}
告警详情: {_safe_json_dumps(alert, 800)}
证据: {proof_summary}
白名单匹配: {whitelist_summary}
RAG 知识: {rag_summary}
关联事件数: {len(state.get('related_incidents', []))}
资产数: {len(state.get('assets', []))}

{degraded_note}
{insufficient_note}

请输出 JSON 格式的研判结果，包含以下字段:
- "verdict": 结论
- "confidence": 置信度 (0.0-1.0)
- "severity": 严重程度
- "summary": 研判摘要
- "reasoning": 推理过程
- "recommended_actions": [{{"action": "...", "rationale": "...", "execution_level": "manual_confirm"}}]
- "missing_evidence": [缺失证据列表]
- "warnings": [警告信息]
"""

    # 调用 LLM
    raw_text = ""
    token_usage = TokenUsage()
    try:
        import os, httpx, asyncio
        
        from clients.llm_client import _load_provider_config, _build_llm_headers
        
        cfg = _load_provider_config()
        headers = _build_llm_headers(cfg)
        payload = {
            "model": cfg.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        timeout = httpx.Timeout(cfg.read_timeout, connect=cfg.connect_timeout)
        async with httpx.AsyncClient(base_url=cfg.base_url, timeout=timeout) as client:
            resp = await client.post("/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
        raw_text = body["choices"][0]["message"]["content"] if body.get("choices") else ""
        token_usage = TokenUsage(
            input_tokens=int(body.get("usage", {}).get("prompt_tokens") or 0),
            output_tokens=int(body.get("usage", {}).get("completion_tokens") or 0),
        )
        llm_model = str(body.get("model") or cfg.model_id)
        llm_resp = {"content": raw_text, "model": llm_model, "input_tokens": token_usage.input_tokens, "output_tokens": token_usage.output_tokens}
    except Exception as e:
        logger.warning("LLM call failed task_id=%s: %s", task_id, e)
        state["warnings"].append(f"LLM_CALL_FAILED: {e}")
        return {
            **state,
            "status": "FAILED",
            "error": f"LLM decision failed: {e}",
            "trace_events": _append_trace(
                state,
                {"type": "make_decision.error", "error": str(e)},
            ),
        }

    # 解析 LLM JSON 输出
    try:
        raw_decision = _parse_llm_json(raw_text)
        raw_decision["_model"] = llm_resp.get("model", "")
        raw_decision["_token_usage"] = token_usage.model_dump()
        state["raw_decision"] = raw_decision
    except (json.JSONDecodeError, ValueError) as e:
        state["warnings"].append(f"LLM_OUTPUT_PARSE_FAILED: {e}")
        state["raw_decision"] = {
            "verdict": "insufficient_evidence",
            "confidence": 0.3,
            "severity": alert_severity,
            "summary": "LLM 输出无法解析，标记为证据不足",
            "reasoning": f"LLM raw output: {raw_text[:500]}",
            "recommended_actions": [],
            "missing_evidence": ["llm_output_unparseable"],
            "warnings": [f"LLM_OUTPUT_PARSE_FAILED"],
        }

    await _emit(
        task_id,
        "AT_MAKE_DECISION_COMPLETE",
        {"verdict": state["raw_decision"].get("verdict")},
    )
    return {
        **state,
        "trace_events": _append_trace(
            state,
            {
                "type": "make_decision.complete",
                "verdict": state["raw_decision"].get("verdict"),
            },
        ),
    }
make_decision._is_async = True  # type: ignore[attr-defined]


# ── Node: validate_decision ─────────────────────────────────────────


async def validate_decision(state: TriageState) -> TriageState:
    """检查字段完整性、证据引用和置信度合理性。"""
    task_id = state["task_id"]
    state["current_node"] = "validate_decision"
    state["validation_errors"] = []

    await _emit(task_id, "AT_VALIDATE_DECISION_START", {})

    raw = state.get("raw_decision") or {}
    errors: list[str] = []

    # 1) 检查必填字段
    verdict = str(raw.get("verdict") or "").strip().lower()
    valid_verdicts = {"true_positive", "false_positive", "suspicious", "insufficient_evidence"}
    if verdict not in valid_verdicts:
        errors.append(f"invalid verdict: {verdict}")
        raw["verdict"] = "insufficient_evidence"

    confidence = float(raw.get("confidence") or 0)
    if not (0.0 <= confidence <= 1.0):
        errors.append(f"invalid confidence: {confidence}")
        raw["confidence"] = 0.3

    severity = str(raw.get("severity") or "medium").strip().lower()
    valid_severities = {"critical", "high", "medium", "low", "info"}
    if severity not in valid_severities:
        raw["severity"] = "medium"

    # 2) evidence 不足时 confidence cap
    missing = state.get("missing_evidence", [])
    if missing and confidence > 0.5:
        errors.append(f"confidence {confidence} too high with missing evidence {missing}")
        raw["confidence"] = 0.4

    if verdict == "insufficient_evidence" and confidence > 0.5:
        errors.append(f"insufficient_evidence with confidence {confidence} > 0.5")
        raw["confidence"] = 0.4

    # 3) 建议动作 execution_level 校验
    actions = raw.get("recommended_actions") or []
    if isinstance(actions, list):
        for act in actions:
            if isinstance(act, dict):
                level = str(act.get("execution_level") or "manual_confirm")
                if level not in ("manual_confirm", "forbidden"):
                    act["execution_level"] = "manual_confirm"
                    errors.append(f"reverted invalid execution_level: {level}")

    # 4) 证据不足时禁止输出 high confidence 确定结论
    if missing and verdict in ("true_positive", "false_positive"):
        if not errors:
            errors.append("deterministic verdict with missing evidence")
        raw["verdict"] = "suspicious"

    state["validation_errors"] = errors
    state["raw_decision"] = raw

    await _emit(
        task_id,
        "AT_VALIDATE_DECISION_COMPLETE",
        {
            "error_count": len(errors),
            "errors": errors[:5],
        },
    )
    return {
        **state,
        "trace_events": _append_trace(
            state,
            {
                "type": "validate_decision.complete",
                "errors": errors,
            },
        ),
    }
validate_decision._is_async = True  # type: ignore[attr-defined]


# ── Node: persist_result ────────────────────────────────────────────


async def persist_result(state: TriageState) -> TriageState:
    """组装最终结构化结果，写入 Trace 和报告。"""
    task_id = state["task_id"]
    state["current_node"] = "persist_result"

    await _emit(task_id, "AT_PERSIST_RESULT_START", {})

    raw = state.get("raw_decision") or {}
    token_data = raw.get("_token_usage") or {}

    # 组装证据引用
    evidence_refs: list[dict[str, Any]] = [
        {"source": "alert", "uuid": state["alert_uuid"], "field": "alert", "value_summary": _summarize_alert(state.get("alert"))}
    ]
    if state.get("alert_proof"):
        evidence_refs.append(
            {"source": "proof", "uuid": state["alert_uuid"], "field": "proof", "value_summary": _summarize_proof(state["alert_proof"])}
        )
    for inc in state.get("related_incidents") or []:
        eid = str(inc.get("uuid") or inc.get("id", ""))
        if eid:
            evidence_refs.append(
                {"source": "incident", "uuid": eid, "field": "", "value_summary": str(inc.get("title") or inc.get("name", "") or "")[:200]}
            )

    # 组装 RAG citations
    rag_data = state.get("rag_response") or {}
    rag_citations_raw: list[dict[str, Any]] = rag_data.get("citations", []) or []

    # 组装 whitelist
    matched_whitelists = [
        str(m.get("id") or m.get("rule_id") or "")
        for m in (state.get("whitelist_matches") or [])
    ]

    # related incidents
    related_incidents = [
        str(inc.get("uuid") or inc.get("id", ""))
        for inc in (state.get("related_incidents") or [])
    ]

    # model
    model = str(raw.get("_model") or "")

    try:
        result = AlertTriageResult(
            alert_uuid=state["alert_uuid"],
            verdict=AlertVerdict(str(raw.get("verdict") or "insufficient_evidence")),
            confidence=float(raw.get("confidence") or 0),
            severity=str(raw.get("severity") or "medium"),
            summary=str(raw.get("summary") or ""),
            reasoning=str(raw.get("reasoning") or ""),
            xdr_evidence_refs=[XdrEvidenceRef(**r) for r in evidence_refs],
            rag_citations=[RagCitation(**c) for c in rag_citations_raw],
            matched_whitelists=matched_whitelists,
            related_incidents=related_incidents,
            recommended_actions=[
                RecommendedAction(**a)
                for a in (raw.get("recommended_actions") or [])
                if isinstance(a, dict)
            ],
            missing_evidence=list(
                set(state.get("missing_evidence", []) + (raw.get("missing_evidence") or []))
            ),
            warnings=list(set(state.get("warnings", []) + (raw.get("warnings") or []))),
            model=model,
            token_usage=TokenUsage(
                input_tokens=int(token_data.get("input_tokens") or 0),
                output_tokens=int(token_data.get("output_tokens") or 0),
            ),
            started_at=state.get("started_at", _now_iso()),
            finished_at=_now_iso(),
        )
        state["result"] = result.model_dump()
    except Exception as e:
        logger.warning("persist_result schema validation failed task_id=%s: %s", task_id, e)
        state["error"] = f"Schema validation failed: {e}"
        state["status"] = "FAILED"
        state["warnings"].append(f"SCHEMA_VALIDATION_FAILED: {e}")
        # 尝试手动构建
        state["result"] = _build_fallback_result(state, str(e))

    await _emit(
        task_id,
        "AT_PERSIST_RESULT_COMPLETE",
        {
            "verdict": raw.get("verdict"),
            "confidence": raw.get("confidence"),
        },
    )
    return {
        **state,
        "trace_events": _append_trace(
            state,
            {
                "type": "persist_result.complete",
                "has_result": state["result"] is not None,
            },
        ),
    }
persist_result._is_async = True  # type: ignore[attr-defined]


# ── Node: recommend_action ──────────────────────────────────────────


async def recommend_action(state: TriageState) -> TriageState:
    """最终确认建议动作，确保 MVP 只输出建议不执行任何写操作。"""
    task_id = state["task_id"]
    state["current_node"] = "recommend_action"

    await _emit(task_id, "AT_RECOMMEND_ACTION_START", {})

    result = state.get("result") or {}
    actions = result.get("recommended_actions") or []

    # 确保 MVP 安全边界: 所有建议动作 execution_level 不能为 auto
    safe_actions: list[dict[str, Any]] = []
    for act in actions:
        if not isinstance(act, dict):
            continue
        level = str(act.get("execution_level") or "")
        if level == "auto":
            act["execution_level"] = "manual_confirm"
            state["warnings"].append(
                f"ACTION_LEVEL_DEMOTED: {act.get('action', 'unknown')} auto→manual_confirm"
            )
        safe_actions.append(act)

    result["recommended_actions"] = safe_actions

    # 追加安全声明
    safe_note = (
        "本研判结果仅提供建议，所有处置动作需人工确认后执行。"
        "Agent 未执行任何封禁、隔离、加白或关闭告警等写操作。"
    )
    result["_safety_note"] = safe_note

    state["result"] = result
    state["status"] = "DONE"
    state["finished_at"] = _now_iso()

    await _emit(
        task_id,
        "AT_RECOMMEND_ACTION_COMPLETE",
        {
            "action_count": len(safe_actions),
            "status": "DONE",
        },
    )
    return {
        **state,
        "trace_events": _append_trace(
            state,
            {
                "type": "recommend_action.complete",
                "action_count": len(safe_actions),
            },
        ),
    }
recommend_action._is_async = True  # type: ignore[attr-defined]


# ── Conditional Routing ─────────────────────────────────────────────


def route_after_load(state: TriageState) -> Literal["collect_evidence", "end"]:
    if state.get("status") == "FAILED":
        return "end"
    return "collect_evidence"


def route_after_collect(state: TriageState) -> str:
    if state.get("status") == "FAILED":
        return "end"
    return "check_whitelist"


def route_after_whitelist(state: TriageState) -> str:
    if state.get("status") == "FAILED":
        return "end"
    return "query_rag"


def route_after_rag(state: TriageState) -> str:
    if state.get("status") == "FAILED":
        return "end"
    return "make_decision"


def route_after_decision(state: TriageState) -> str:
    if state.get("status") == "FAILED":
        return "end"
    return "validate_decision"


def route_after_validate(state: TriageState) -> str:
    if state.get("status") == "FAILED":
        return "end"
    return "persist_result"


def route_after_persist(state: TriageState) -> str:
    if state.get("status") == "FAILED":
        return "end"
    return "recommend_action"


# ── Helpers ─────────────────────────────────────────────────────────


def _summarize_proof(proof: dict[str, Any]) -> str:
    """将 proof dict 压缩为一句话摘要。"""
    if not proof:
        return "(无)"
    items = []
    for k, v in proof.items():
        if isinstance(v, str) and len(v) > 120:
            v = v[:120] + "..."
        items.append(f"{k}: {v}")
    return "; ".join(items[:10])


def _summarize_alert(alert: dict[str, Any] | None) -> str:
    if not alert:
        return "(无)"
    name = alert.get("name") or alert.get("title") or alert.get("alert_type") or ""
    sev = alert.get("severity") or ""
    return f"{name} (severity={sev})"[:200]


def _safe_json_dumps(obj: Any, max_len: int = 2000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
        return s[:max_len]
    except Exception:
        return str(obj)[:max_len]


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """从 LLM 输出中提取 JSON 对象。"""
    if not raw or not raw.strip():
        raise ValueError("empty LLM output")

    # 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    import re
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        return json.loads(match.group(1).strip())

    # 尝试查找第一个 { ... } 块
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])

    raise ValueError(f"cannot parse JSON from LLM output: {raw[:200]}")


def _build_fallback_result(state: TriageState, error: str) -> dict[str, Any]:
    """Schema 验证失败时手动构建降级结果。"""
    raw = state.get("raw_decision") or {}
    return {
        "alert_uuid": state["alert_uuid"],
        "verdict": "insufficient_evidence",
        "confidence": 0.3,
        "severity": str(raw.get("severity") or "medium"),
        "summary": f"研判结果校验失败: {error}",
        "reasoning": str(raw.get("reasoning") or ""),
        "xdr_evidence_refs": [],
        "rag_citations": [],
        "matched_whitelists": [],
        "related_incidents": [],
        "recommended_actions": [],
        "missing_evidence": ["schema_validation_failed"],
        "warnings": state.get("warnings", []) + [f"SCHEMA_VALIDATION_FAILED: {error}"],
        "model": "",
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
        "started_at": state.get("started_at", _now_iso()),
        "finished_at": _now_iso(),
    }


# ── Graph Builder ───────────────────────────────────────────────────


def build_graph() -> Any:
    """构建告警研判 LangGraph 状态图。"""
    graph = StateGraph(TriageState)

    graph.add_node("load_alert", load_alert)
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("check_whitelist", check_whitelist)
    graph.add_node("query_rag", query_rag)
    graph.add_node("make_decision", make_decision)
    graph.add_node("validate_decision", validate_decision)
    graph.add_node("persist_result", persist_result)
    graph.add_node("recommend_action", recommend_action)

    graph.set_entry_point("load_alert")

    graph.add_conditional_edges(
        "load_alert",
        route_after_load,
        {"collect_evidence": "collect_evidence", "end": END},
    )
    graph.add_conditional_edges(
        "collect_evidence",
        route_after_collect,
        {"check_whitelist": "check_whitelist", "end": END},
    )
    graph.add_conditional_edges(
        "check_whitelist",
        route_after_whitelist,
        {"query_rag": "query_rag", "end": END},
    )
    graph.add_conditional_edges(
        "query_rag",
        route_after_rag,
        {"make_decision": "make_decision", "end": END},
    )
    graph.add_conditional_edges(
        "make_decision",
        route_after_decision,
        {"validate_decision": "validate_decision", "end": END},
    )
    graph.add_conditional_edges(
        "validate_decision",
        route_after_validate,
        {"persist_result": "persist_result", "end": END},
    )
    graph.add_conditional_edges(
        "persist_result",
        route_after_persist,
        {"recommend_action": "recommend_action", "end": END},
    )
    graph.add_edge("recommend_action", END)

    return graph.compile()


async def run_alert_triage(req: dict[str, Any]) -> dict[str, Any]:
    """执行告警研判工作流。

    Args:
        req: {
            "task_id": str,
            "alert_uuid": str,
            "scenario_id": str | None,
            "enable_rag": bool,
            "caller_notes": str | None,
        }

    Returns:
        研判结果 dict，包含 result（AlertTriageResult 序列化）和状态信息。
    """
    task_id = str(req.get("task_id") or f"at-{_now_iso()}")
    alert_uuid = str(req["alert_uuid"])
    initial: TriageState = {
        "task_id": task_id,
        "alert_uuid": alert_uuid,
        "scenario_id": req.get("scenario_id"),
        "enable_rag": bool(req.get("enable_rag", True)),
        "caller_notes": req.get("caller_notes"),
        "alert": None,
        "alert_proof": None,
        "assets": [],
        "related_incidents": [],
        "incident_proofs": [],
        "whitelist_matches": [],
        "rag_response": None,
        "rag_degraded": False,
        "raw_decision": None,
        "validation_errors": [],
        "result": None,
        "status": "RUNNING",
        "error": None,
        "warnings": [],
        "missing_evidence": [],
        "started_at": _now_iso(),
        "finished_at": "",
        "current_node": "init",
        "trace_events": [
            {
                "type": "alert_triage.start",
                "task_id": task_id,
                "alert_uuid": alert_uuid,
                "enable_rag": req.get("enable_rag", True),
            }
        ],
    }

    await _emit(
        task_id,
        "AT_WORKFLOW_START",
        {"alert_uuid": alert_uuid, "enable_rag": req.get("enable_rag", True)},
    )

    final = await build_graph().ainvoke(initial, {"recursion_limit": 25})

    await _emit(
        task_id,
        "AT_WORKFLOW_COMPLETE",
        {
            "status": final.get("status"),
            "verdict": (final.get("result") or {}).get("verdict"),
        },
    )

    return {
        "task_id": task_id,
        "alert_uuid": alert_uuid,
        "status": str(final.get("status") or "FAILED"),
        "result": final.get("result"),
        "error": final.get("error"),
        "warnings": final.get("warnings", []),
        "trace_events": final.get("trace_events", []),
    }
