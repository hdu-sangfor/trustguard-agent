from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any
import copy
from urllib.parse import urlparse

from app.clients.evidence_client import put_artifacts_summary, put_context
from app.clients.executor_client import fetch_executor_artifact
from app.core.execution_dispatcher import ExecutionDispatcher, get_execution_dispatcher
from app.core.http_enum_seeds import extract_http_enum_seed_urls
from app.core.artifact_reader import load_parsed_from_artifact_ref, get_event_id_from_artifact_ref
from app.core.workspace_store import write_artifact, write_task_context, write_memory_parsed_artifact
from app.models import Phase, TaskState
from app.core.correlation_ids import attach_correlation, correlation_dict
from app.structured_error_envelope import structured_error_for_skill_dispatch
from app.core.memory_store import (
    append_action_ledger,
    build_context_snapshot_for_put,
    classify_endpoint_with_baseline,
    is_crawler_confirmed_url,
    ensure_http_fallback_baseline,
    infer_http_probe_targets,
    update_entity_blackboard,
    add_tier1_fact,
)

logger = logging.getLogger(__name__)

# 视为「非失败」的状态：OK/SUCCESS=执行成功。
# 注意：MQ 轮询已前移到 ExecutionDispatcher，编排器应只收到终态，不再消费 DISPATCHED。
_NON_ERROR_STATUSES = frozenset({"ok", "success"})
_ENABLE_FLAT_CONTEXT_COMPAT = os.getenv("MEMORY_V1_ENABLE_FLAT_CONTEXT_COMPAT", "false").strip().lower() == "true"

# Context Window 防御：写入上下文/摘要时的截断上限（字符数），避免多工具并发后 prompt 超限
_ARTIFACT_VALUE_MAX_CHARS = 2000
_CONTEXT_PUT_MAX_CHARS = 60000

# Struts2 S2-NNN 编号提取：从 nuclei 模板 id/名称中精确截取形如 "s2-045" 的片段，
# 避免把整个模板描述文本（如 "local struts2 s2-045 command echo probe"）当作 CVE 号写入
# confirmed_facts（已在 task-5d1c3092... 观察到 "S2-045 COMMAND ECHO PROB" 这种脏数据）。
_S2_ID_PATTERN = re.compile(r"s2-\d+", re.IGNORECASE)
# CVE-YYYY-NNNN[N] 形式的标准 CVE 编号
_CVE_ID_PATTERN = re.compile(r"cve-(\d{4})-(\d{4,7})", re.IGNORECASE)


def _backflow_nuclei_confirmed_facts(state: TaskState, resolved_artifacts: dict[str, Any]) -> None:
    """
    将 nuclei 的确权结果回流到黑板事实，作为 EXPLOIT 门禁的可验证输入。

    修复说明（三处缺陷）：
    1. 原代码忽略 vulns[].cve 字段（来自 classification.cve-id），该字段已含完整 CVE 号；
    2. S2 类漏洞模板 ID 通常为 "struts/s2-057" 或 "apache-struts-s2-057"，
       startswith("s2-") 判断失效；改为 "s2-" 是否在 template_id 任意位置；
    3. 当 nuclei 扫到 critical/high 漏洞但提取不出 CVE 号时（如 0-day 或自定义模板），
       原代码直接 return 而不设置 exploit_ready/vuln_confirmed，导致相态门禁永久阻塞；
       现在对任何 critical/high 漏洞均解锁门禁标志。
    """
    vulns = resolved_artifacts.get("vulnerabilities")
    if not isinstance(vulns, list) or not vulns:
        return
    if not isinstance(state.target_context, dict):
        state.target_context = {}

    cves: list[str] = []
    has_critical_or_high = False

    for item in vulns[:50]:
        if not isinstance(item, dict):
            continue

        sev = str(item.get("severity") or "").strip().lower()
        if sev in ("critical", "high"):
            has_critical_or_high = True

        template_id = str(item.get("template_id") or item.get("template") or "").strip()
        template_id_lower = template_id.lower()
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        name = str(info.get("name") or "")
        cve_id = ""

        # 优先：vulns[].cve 字段直接携带 CVE 编号（来自 nuclei classification.cve-id）
        raw_cve = str(item.get("cve") or "").strip().upper()
        if raw_cve.startswith("CVE-"):
            cve_id = raw_cve
        elif raw_cve:
            # 有时是逗号分隔的多个 CVE
            for part in raw_cve.split(","):
                p = part.strip()
                if p.startswith("CVE-"):
                    cve_id = p
                    break

        # 次优：在 template_id 中用严格正则匹配 "cve-YYYY-NNNN" 形式
        if not cve_id:
            m = _CVE_ID_PATTERN.search(template_id_lower)
            if m:
                cve_id = f"CVE-{m.group(1)}-{m.group(2)}"

        # 次优：在模板名称 info.name 中同样用严格正则
        if not cve_id:
            m = _CVE_ID_PATTERN.search(name.lower())
            if m:
                cve_id = f"CVE-{m.group(1)}-{m.group(2)}"

        # Struts2 专项：用精确正则截取 "s2-NNN"，避免把模板描述误当作 ID。
        # 兼容 "struts/s2-057"、"apache-struts-s2-057-rce"、
        # "Local Struts2 S2-045 Command Echo Probe" 等多种命名。
        if not cve_id:
            m = _S2_ID_PATTERN.search(template_id_lower) or _S2_ID_PATTERN.search(name.lower())
            if m:
                cve_id = m.group(0).upper()  # 如 "S2-057" / "S2-045"

        if cve_id:
            cves.append(cve_id)

    uniq: list[str] = []
    seen: set[str] = set()
    for c in cves:
        u = c.strip().upper()
        if not u or u in seen:
            continue
        seen.add(u)
        uniq.append(u)

    # 无论是否能提取到 CVE 编号，只要有 critical/high 漏洞就应解锁 EXPLOIT 门禁。
    # 两条路径都需设置；下方 confirmed_facts 仅在有 CVE 时追加。
    if not uniq and not has_critical_or_high:
        return

    existing = [str(x).strip() for x in (getattr(state, "confirmed_facts", None) or []) if str(x).strip()]
    if uniq:
        add_lines = [f"confirmed_cve: {u}" for u in uniq]
        for line in add_lines:
            if line not in existing:
                existing.append(line)
        state.confirmed_facts = existing[-160:]
        state.target_context["confirmed_cve"] = uniq
    elif has_critical_or_high:
        # 没有 CVE 编号但有高危发现：写入匿名事实，避免后续 LLM 困惑
        marker = "vuln_confirmed: critical_or_high_severity_finding"
        if marker not in existing:
            existing.append(marker)
        state.confirmed_facts = existing[-160:]

    state.target_context["vulnerability_confirmed"] = True
    state.target_context["vuln_confirmed"] = True
    state.target_context["exploit_ready"] = True


def _backflow_high_value_facts(state: TaskState, resolved_artifacts: dict[str, Any]) -> None:
    facts = resolved_artifacts.get("high_value_facts")
    if not isinstance(facts, list) or not facts:
        return
    existing = [str(x).strip() for x in (getattr(state, "confirmed_facts", None) or []) if str(x).strip()]
    for f in facts[:20]:
        s = str(f).strip()
        if not s:
            continue
        if s not in existing:
            existing.append(s)
    state.confirmed_facts = existing[-200:]


async def _load_parsed_from_artifact_ref_with_retry(
    artifact_ref: str, *, attempts: int = 6, wait_seconds: float = 0.2
) -> dict[str, Any]:
    """
    Executor 落盘 parsed.json 后，编排器偶尔瞬间读不到（NFS/桌面 Docker I/O 抖动）。
    与 ExecutionDispatcher._load_parsed_with_retry 对齐思路。
    """
    max_attempts = max(1, int(os.getenv("ORCH_ARTIFACT_READ_MAX_RETRIES", str(attempts))))
    base_wait = float(os.getenv("ORCH_ARTIFACT_READ_WAIT_SECONDS", str(wait_seconds)))
    for i in range(max_attempts):
        data = load_parsed_from_artifact_ref(artifact_ref)
        if data:
            return data
        if i + 1 < max_attempts:
            await asyncio.sleep(min(2.0, base_wait * (2**i)))
    return {}


def _normalize_invalid_target_port(target: str, allowed_target: str) -> str:
    """
    修复 LLM 偶发产出的 :0 端口目标，避免技能容器访问 host.docker.internal:0 导致伪失败。
    优先回退到 allowed_target（任务授权目标）。
    """
    t = (target or "").strip()
    if not t or "://" not in t:
        return t
    try:
        p = urlparse(t)
        if p.port == 0:
            return (allowed_target or "").strip() or t
        return t
    except Exception:
        return t


def _resolve_memory_event_id(
    *,
    artifact_ref: str,
    call_ctx_request_id: str | None,
    exec_result_request_id: str | None,
) -> str:
    event_id = get_event_id_from_artifact_ref(artifact_ref) if artifact_ref else ""
    if event_id:
        return event_id
    rid = str(call_ctx_request_id or exec_result_request_id or "").strip()
    if rid:
        return "evt-" + rid
    return "evt-" + uuid.uuid4().hex[:12]


def _extract_artifact_returncode(exec_result: Any, resolved_artifacts: dict[str, Any]) -> int | None:
    """优先从 parsed_artifacts 提取底层 returncode，做执行状态二次校验。"""
    try:
        pa = getattr(exec_result, "parsed_artifacts", None) if exec_result is not None else None
    except Exception:
        pa = None
    if not isinstance(pa, dict):
        pa = resolved_artifacts if isinstance(resolved_artifacts, dict) else {}
    raw = pa.get("returncode") if isinstance(pa, dict) else None
    try:
        return int(raw) if raw is not None else None
    except Exception:
        return None


# 工具侧已显式标注「非 0 退出码为良性」的 quirk 标记；命中任一即视为可信的 SUCCESS，
# 不应被 _extract_artifact_returncode + has_fatal_returncode 机制覆盖回 FAILED。
_BENIGN_RETURNCODE_MARKERS: tuple[str, ...] = (
    "ehole_exit_quirk_note",        # ehole finger 固定 os.Exit(1)
    "returncode_benign",             # 通用显式标记（工具可主动置 True）
    "status_masked_by_payload",      # executor 已经处理过 mask 策略
)


_DISPATCHER_PREPARE_CACHE_KEY = "_dispatcher_prepare_cache"
# dispatcher prepare 是确定性 ETL（同 seed URL + 同 fingerprint 必产相同 chunk），
# 缓存命中 TTL：超过 TTL 强制重算以反映目标变更。
_DISPATCHER_PREPARE_CACHE_TTL_SECONDS = 600
# 单任务最多缓存的 dispatcher prepare 条目数，防止 target_context 膨胀
_DISPATCHER_PREPARE_CACHE_MAX_ENTRIES = 16


def _canonical_dispatcher_cache_key(skill_id: str, target: str, params: dict[str, Any]) -> str:
    """
    为 dispatcher prepare 构造稳定缓存 key：仅纳入影响 ETL 输出的字段，
    剔除：
    - target：已单独作为 key 组件
    - timeout：不影响确定性输出
    - run_id / output_discovery_dir：LLM 每次会幻觉出不同值但实际指向同一 katana 输出；
      包含在 key 里会让缓存永远 miss（已在 task-5d1c3092... 观察到：每次 dispatcher
      调用 run_id 都不同，缓存 0 命中率，触发 LOOP_BREAK）
    """
    import hashlib

    volatile_keys = frozenset({"timeout", "target", "run_id", "output_discovery_dir", "output_dir"})
    stable_params = {k: v for k, v in (params or {}).items() if k not in volatile_keys}
    try:
        payload = json.dumps(
            {"skill_id": skill_id, "target": target, "params": stable_params},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        payload = f"{skill_id}|{target}|{sorted((stable_params or {}).keys())}"
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _is_dispatcher_prepare(skill_id: str, params: dict[str, Any]) -> bool:
    if (skill_id or "").strip().lower() != "dispatcher":
        return False
    op = str((params or {}).get("operation") or "prepare").strip().lower()
    return op == "prepare"


def _lookup_dispatcher_prepare_cache(
    state: TaskState, cache_key: str
) -> dict[str, Any] | None:
    """返回缓存条目（含 resolved_artifacts / artifact_ref / created_at），未命中/过期则 None。"""
    import time as _time

    ctx = state.target_context if isinstance(state.target_context, dict) else {}
    cache = ctx.get(_DISPATCHER_PREPARE_CACHE_KEY)
    if not isinstance(cache, dict):
        return None
    entry = cache.get(cache_key)
    if not isinstance(entry, dict):
        return None
    created = entry.get("_cached_at") or 0
    try:
        created_ts = float(created)
    except (TypeError, ValueError):
        return None
    if _time.time() - created_ts > _DISPATCHER_PREPARE_CACHE_TTL_SECONDS:
        return None
    return entry


def _store_dispatcher_prepare_cache(
    state: TaskState,
    cache_key: str,
    *,
    artifact_ref: str,
    resolved_artifacts: dict[str, Any],
    exec_status: str,
) -> None:
    import time as _time

    if not isinstance(state.target_context, dict):
        state.target_context = {}
    cache = state.target_context.get(_DISPATCHER_PREPARE_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
    # 仅缓存 SUCCESS 结果，避免失败结果毒化后续调用
    if exec_status.strip().lower() not in _NON_ERROR_STATUSES:
        return
    # 拷贝一份精简的 resolved_artifacts（移除 raw 字段防止上下文膨胀）
    trimmed = {
        k: v
        for k, v in (resolved_artifacts or {}).items()
        if k not in ("raw_stdout", "raw_stderr", "raw_preview")
    }
    cache[cache_key] = {
        "_cached_at": _time.time(),
        "artifact_ref": artifact_ref,
        "resolved_artifacts": trimmed,
    }
    # LRU：按 _cached_at 淘汰最老条目
    if len(cache) > _DISPATCHER_PREPARE_CACHE_MAX_ENTRIES:
        try:
            sorted_keys = sorted(
                cache.keys(),
                key=lambda k: float((cache.get(k) or {}).get("_cached_at") or 0),
            )
            for old_k in sorted_keys[: len(cache) - _DISPATCHER_PREPARE_CACHE_MAX_ENTRIES]:
                cache.pop(old_k, None)
        except Exception:
            pass
    state.target_context[_DISPATCHER_PREPARE_CACHE_KEY] = cache


def _has_benign_returncode_marker(parsed_artifacts: dict[str, Any]) -> bool:
    """
    工具侧已主动声明 rc != 0 为良性（如 ehole finger 的 os.Exit(1) quirk）时，
    编排器不应再用 has_fatal_returncode 把 status 覆盖回 FAILED，否则会污染
    系统提示并误导后续 LLM 决策（已在 task-0385... 日志中观察到）。
    """
    if not isinstance(parsed_artifacts, dict) or not parsed_artifacts:
        return False
    for key in _BENIGN_RETURNCODE_MARKERS:
        v = parsed_artifacts.get(key)
        if v is True:
            return True
        if isinstance(v, str) and v.strip():
            # 有解释性说明字符串即视为显式声明
            return True
    return False


@dataclass
class SkillCallContext:
    """一次技能执行调用所需的关键上下文字段。"""

    task_id: str
    phase: Phase
    skill_id: str
    target: str
    params: dict[str, Any]
    allowed_target: str
    # 多 Agent 扩展钩子：当前可为空，后续 Manager/Todo/MQ Worker 可填充
    agent_role: str | None = None
    todo_id: str | None = None
    request_id: str | None = None
    plan_id: str | None = None


_ARTIFACTS_CTX_KEY_BLACKLIST = (
    "raw_stdout",
    "raw_stderr",
)

_NAMESPACED_CONTEXT_KEYS_BY_SKILL: dict[str, frozenset[str]] = {
    "http-enum": frozenset({"url", "http_status", "headers", "title", "raw_preview", "returncode"}),
    "curl-raw": frozenset({"status", "status_code", "http_status", "headers", "raw_preview", "returncode"}),
    "whatweb-fingerprint": frozenset({"fingerprints", "raw_preview", "title", "tech_stack", "returncode"}),
    "httpx": frozenset({"input_url", "url", "status_code", "title", "tech_stack", "server", "x_powered_by"}),
}


def _persist_namespaced_context_keys(
    target_context: dict[str, Any],
    *,
    skill_id: str,
    artifacts: dict[str, Any],
) -> None:
    sid = (skill_id or "").strip().lower()
    allowed = _NAMESPACED_CONTEXT_KEYS_BY_SKILL.get(sid)
    if not allowed:
        return
    for key in sorted(allowed):
        if key not in artifacts:
            continue
        value = artifacts.get(key)
        if value is None:
            continue
        target_context[f"{sid}_{key}"] = value


def _truncate_artifacts_for_context(artifacts: dict[str, Any], max_value_chars: int = _ARTIFACT_VALUE_MAX_CHARS) -> dict[str, Any]:
    """
    将 artifacts 的「干净副本」截断后用于写入 target_context，避免单 Tick 多工具结果导致 prompt 超限。
    - 对原始 resolved_artifacts 保持只读（不删除 raw 日志等字段，保证落盘完整性）
    - 在副本上过滤 raw_stdout/raw_stderr 等黑名单键，并截断长字符串/列表
    """
    def _inner(src: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in (src or {}).items():
            if isinstance(k, str) and k in _ARTIFACTS_CTX_KEY_BLACKLIST:
                continue
            if isinstance(v, str):
                out[k] = v if len(v) <= max_value_chars else v[:max_value_chars] + "...[truncated]"
            elif isinstance(v, (int, float, bool)) or v is None:
                out[k] = v
            elif isinstance(v, dict):
                out[k] = _inner(v)
            elif isinstance(v, list):
                out[k] = v[:20] if len(v) > 20 else v
            else:
                sv = str(v)
                out[k] = sv if len(sv) <= max_value_chars else sv[:max_value_chars] + "..."
        return out

    safe_src = artifacts or {}
    try:
        # 防御调用方误传共享引用，始终在副本上过滤/截断
        safe_src = copy.deepcopy(safe_src)
    except Exception:
        pass
    return _inner(safe_src)


def _truncate_context_for_put(context: dict[str, Any], max_value_chars: int = 3000) -> dict[str, Any]:
    """截断 target_context 再 PUT 到Evidence，防止单次请求体过大、下一 Tick prompt 超限。"""
    if not context:
        return context

    def truncate_value(v: Any) -> Any:
        if isinstance(v, str):
            return v[:max_value_chars] + "..." if len(v) > max_value_chars else v
        if isinstance(v, dict):
            return {k: truncate_value(x) for k, x in v.items()}
        if isinstance(v, list):
            return [truncate_value(x) for x in v[:30]]
        return v

    return truncate_value(context)  # type: ignore[return-value]


def _extract_fingerprint_context(target_context: dict[str, Any]) -> dict[str, str]:
    """
    从 orchestrator 已有上下文提取 whatweb/指纹信号，注入到后续 web-vuln-pipeline 的 context。
    仅提取少量字符串，防止上下文膨胀。
    """
    if not isinstance(target_context, dict) or not target_context:
        return {}
    lines: list[str] = []

    def _append(v: Any) -> None:
        if isinstance(v, list):
            for item in v:
                s = str(item).strip()
                if s:
                    lines.append(s)
        elif isinstance(v, str):
            s = v.strip()
            if s:
                lines.append(s)

    _append(target_context.get("fingerprints"))
    _append(target_context.get("whatweb-fingerprint_fingerprints"))
    _append(target_context.get("whatweb-fingerprint_raw_preview"))
    for k, v in target_context.items():
        kl = str(k).lower()
        if "whatweb" in kl and "fingerprint" in kl:
            _append(v)

    # stable de-dup
    uniq: list[str] = []
    seen: set[str] = set()
    for s in lines:
        if s in seen:
            continue
        seen.add(s)
        uniq.append(s)
        if len(uniq) >= 30:
            break
    if not uniq:
        return {}
    merged = "\n".join(uniq)
    if len(merged) > 6000:
        merged = merged[:6000]
    return {"fingerprint": merged, "whatweb": merged}


def fingerprint_signals_present(target_context: dict[str, Any] | None) -> bool:
    """供调度层判断：上下文是否已具备 whatweb/指纹类信号（与 web-vuln-pipeline 注入逻辑同源）。"""
    return bool(_extract_fingerprint_context(target_context or {}))


def get_fingerprint_text_for_pipeline(target_context: dict[str, Any] | None) -> str:
    """Gateway暴露的指纹合并文本（供 IML / 预检使用，与 _extract_fingerprint_context 同源）。"""
    return str((_extract_fingerprint_context(target_context or {}) or {}).get("fingerprint") or "")


class SkillExecutor:
    """
    编排器内部技能执行封装（不负责 Trace/loop_guard，只负责执行与持久化）。

    r4f-b：编排进程内发往执行层的 **dispatch** 均经本类的 `execute_skill_dispatch_only` 汇聚；服务拓扑见 `docs/architecture.md`。

    目标：
    - 将「调用执行器 → 解析 artifacts → 同步上下文/证据」逻辑集中在一处；
    - Trace 事件与 loop_guard 仍由上层 state_machine/Agent 控制，保持语义清晰可控；
    -     为后续 MQ / 多 Worker 等扩展保留统一入口。
    执行派发通过 ExecutionDispatcher 抽象注入，默认按 EXECUTION_DISPATCH_MODE 选择 HTTP/MQ。
    """

    def __init__(self, dispatcher: ExecutionDispatcher | None = None) -> None:
        self._dispatcher = dispatcher or get_execution_dispatcher()

    async def execute_skill_dispatch_only(
        self,
        state: TaskState,
        call_ctx: SkillCallContext,
        *,
        enable_executor: bool,
        available_skills: list[str],
    ) -> dict[str, Any]:
        """
        仅派发执行并解析 artifacts，不写 state / context / workspace。
        用于多任务并发：gather 多个 dispatch_only 后再顺序 apply_execution_result。
        返回与 execute_skill 相同的结构：exec_result, resolved_artifacts, artifact_dir（此处 artifact_dir 为空，由 apply 填充）。
        """
        if not enable_executor:
            return {
                "exec_result": None,
                "resolved_artifacts": {},
                "artifact_dir": "",
            }

        exec_context: dict[str, Any] = {"phase": call_ctx.phase.value}
        if call_ctx.agent_role is not None:
            exec_context["agent_role"] = call_ctx.agent_role
        if call_ctx.todo_id is not None:
            exec_context["todo_id"] = call_ctx.todo_id
        if call_ctx.request_id is not None:
            exec_context["request_id"] = call_ctx.request_id
        if getattr(call_ctx, "plan_id", None):
            exec_context["plan_id"] = str(call_ctx.plan_id).strip()
        # R6d：供派发层按租户限流；不在 EXECUTION_CONTEXT_ALLOWED_KEYS 内，executor_client 发往执行器前会滤掉。
        from app.kb_experience_payload import pick_workspace_scope_from_context

        _ws, _proj = pick_workspace_scope_from_context(state.target_context, include_env_defaults=True)
        if _ws:
            exec_context["workspace_id"] = _ws
        if _proj:
            exec_context["project_id"] = _proj
        if (call_ctx.skill_id or "").strip().lower() == "web-vuln-pipeline":
            exec_context.update(_extract_fingerprint_context(state.target_context))
            if "stack_hint" in (state.target_context or {}) and "stack_hint" not in exec_context:
                exec_context["stack_hint"] = str((state.target_context or {}).get("stack_hint") or "").strip()
        if (call_ctx.skill_id or "").strip().lower() in ("dispatcher", "web-vuln-pipeline"):
            seeds = extract_http_enum_seed_urls(state.target_context or {})
            if seeds:
                exec_context["http_enum_fallback_urls"] = seeds
        normalized_target = _normalize_invalid_target_port(call_ctx.target, call_ctx.allowed_target)
        if normalized_target != call_ctx.target:
            logger.warning(
                "skill_execute normalized invalid target port skill_id=%s from=%s to=%s",
                call_ctx.skill_id,
                call_ctx.target,
                normalized_target,
            )
        from app.core.execution_kind import resolve_execution_kind

        ek = resolve_execution_kind(skill_id=call_ctx.skill_id, execution_kind=None)

        # dispatcher 要求 params.run_id（指向 katana 的 discovery 目录），
        # LLM 常遗漏此字段。在确认 skill 为 dispatcher 且 run_id 缺失时自动从上下文回填，
        # 防止 "params.run_id required (from katana)" 重复失败。
        #
        # 污染防护：优先读取 web_vuln_run_id（katana 产出时 pin 的金标），
        # 再回退到 run_id 键 —— 后者可能已被下游 nuclei/dispatcher 的新 run_id 覆盖。
        # 已在 task-5d1c3092... 观察到 run_id 键被污染为错误 run_id，
        # 导致 dispatcher 在空 discovery 下反复空转、最终触发 LOOP_BREAK。
        effective_params = dict(call_ctx.params or {})
        _sid_lower = (call_ctx.skill_id or "").strip().lower()
        if _sid_lower == "dispatcher":
            if not effective_params.get("run_id"):
                ctx = state.target_context or {}
                ctx_run_id = (
                    str(ctx.get("web_vuln_run_id") or "").strip()
                    or str(ctx.get("run_id") or "").strip()
                )
                if ctx_run_id:
                    effective_params["run_id"] = ctx_run_id
                    logger.info(
                        "skill_execute auto-injected run_id=%s into dispatcher params task_id=%s (source=%s)",
                        ctx_run_id,
                        call_ctx.task_id,
                        "web_vuln_run_id" if ctx.get("web_vuln_run_id") else "run_id",
                    )

        # read_workspace_artifact 缺 artifact_ref 时自动回填：
        # LLM 常遗漏此字段（已在 task-22042fc6... 观察到），导致立即失败并
        # 触发 THREAT_MODEL 阶段 LLM 退化为二次 recon。
        # 回填源：_last_productive_artifact_ref（apply_execution_result 维护的最近成功非读取型 skill 的 ref）
        #
        # 重入保护（task-55cec339... 发现回归）：
        # 同一个 fallback ref 不应在短期内被连续注入两次——否则 LLM 会反复
        # "读自己上次的输出" 形成自噬循环（已观察到 15 次 read_workspace_artifact
        # 中 5 次读同一 dispatcher 输出、9 次在 curl-raw 输出间环绕）。
        # 若 fallback 等于上次自动注入的 ref，则放弃注入、让 skill 侧以清晰
        # 错误信息回复 LLM（LLM 必须显式选 ref），从而跳出循环。
        if _sid_lower == "read_workspace_artifact":
            raw_ref = str(effective_params.get("artifact_ref") or "").strip()
            if not raw_ref:
                ctx = state.target_context or {}
                fallback_ref = str(
                    ctx.get("_last_productive_artifact_ref")
                    or ctx.get("_artifact_ref")
                    or ""
                ).strip()
                last_injected = str(ctx.get("_last_auto_injected_read_ref") or "").strip()
                if fallback_ref.startswith("wsref:") and fallback_ref != last_injected:
                    effective_params["artifact_ref"] = fallback_ref
                    state.target_context["_last_auto_injected_read_ref"] = fallback_ref
                    logger.info(
                        "skill_execute auto-injected artifact_ref=%s into read_workspace_artifact "
                        "params task_id=%s (source=%s)",
                        fallback_ref,
                        call_ctx.task_id,
                        "last_productive" if ctx.get("_last_productive_artifact_ref") else "_artifact_ref",
                    )
                elif fallback_ref and fallback_ref == last_injected:
                    logger.info(
                        "skill_execute SKIPPED auto-inject artifact_ref (same as last injected=%s) "
                        "to avoid self-feeding read loop task_id=%s",
                        fallback_ref,
                        call_ctx.task_id,
                    )

        # dispatcher prepare 幂等缓存：相同 (skill_id, target, 核心 params) 命中 TTL 内的缓存时，
        # 直接构造合成 exec_result 返回，避免重复 docker run / ETL 浪费。
        # 已在 task-0385... 观察到 5+ 次 dispatcher prepare 产出 context_fingerprint=01ba4719...
        # 完全相同的结果，却仍每次真实执行。
        _dispatcher_cache_hit = False
        _dispatcher_cache_key: str | None = None
        if _is_dispatcher_prepare(call_ctx.skill_id, effective_params):
            _dispatcher_cache_key = _canonical_dispatcher_cache_key(
                call_ctx.skill_id, normalized_target, effective_params
            )
            _cached_entry = _lookup_dispatcher_prepare_cache(state, _dispatcher_cache_key)
            if _cached_entry is not None:
                cached_artifacts = dict(_cached_entry.get("resolved_artifacts") or {})
                cached_ref = str(_cached_entry.get("artifact_ref") or "").strip()
                cached_artifacts["_dispatcher_cache_hit"] = True
                if cached_ref:
                    cached_artifacts.setdefault("_artifact_ref", cached_ref)
                    cached_artifacts.setdefault("artifact_ref", cached_ref)
                from app.models import ExecuteSkillResponse

                exec_result = ExecuteSkillResponse(
                    status="SUCCESS",
                    parsed_artifacts=cached_artifacts,
                    raw_stdout="",
                    raw_stderr="",
                    duration_ms=0,
                    request_id=call_ctx.request_id,
                )
                logger.info(
                    "dispatcher prepare cache HIT task_id=%s key=%s (skipping executor dispatch)",
                    call_ctx.task_id,
                    _dispatcher_cache_key,
                )
                _dispatcher_cache_hit = True

        if not _dispatcher_cache_hit:
            exec_result = await self._dispatcher.dispatch(
                task_id=call_ctx.task_id,
                skill_id=call_ctx.skill_id,
                target=normalized_target,
                params=effective_params,
                allowed_target=call_ctx.allowed_target,
                context=exec_context,
                execution_kind=ek,
            )

        artifact_ref = str((exec_result.parsed_artifacts or {}).get("artifact_ref") or "")
        resolved_artifacts: dict[str, Any] = exec_result.parsed_artifacts or {}
        event_id = ""
        file_missing = False
        if artifact_ref:
            event_id = get_event_id_from_artifact_ref(artifact_ref)
            parsed_from_fs = await _load_parsed_from_artifact_ref_with_retry(artifact_ref)
            if parsed_from_fs:
                resolved_artifacts = parsed_from_fs
                resolved_artifacts["_artifact_ref"] = artifact_ref
            else:
                # 兜底：当 orchestrator/executor 挂载路径不一致或短暂读盘竞态时，走 Executor API 再取一次。
                remote = await fetch_executor_artifact(task_id=call_ctx.task_id, artifact_ref=artifact_ref, include_raw=False)
                parsed_remote = ((remote or {}).get("parsed") or {}) if isinstance(remote, dict) else {}
                if isinstance(parsed_remote, dict) and parsed_remote:
                    resolved_artifacts = dict(parsed_remote)
                    resolved_artifacts["_artifact_ref"] = artifact_ref
                    resolved_artifacts["_artifact_resolved_via"] = "executor_api_fallback"
                else:
                    file_missing = True

        status = (exec_result.status or "").strip()
        status_lower = status.lower()
        is_error_status = bool(status and status_lower not in _NON_ERROR_STATUSES)
        logger.info(
            "skill_execute result task_id=%s skill_id=%s status=%s artifact_ref=%s file_missing=%s is_error_status=%s",
            call_ctx.task_id, call_ctx.skill_id, status, bool(artifact_ref), file_missing, is_error_status,
        )
        if status_lower == "dispatched" and not resolved_artifacts and not file_missing:
            resolved_artifacts = {
                "status": "DISPATCHED",
                "message": "任务已下发至队列，等待 Worker 执行并写回结果。",
            }
        elif not resolved_artifacts or file_missing or is_error_status:
            hint = ""
            ra = resolved_artifacts if isinstance(resolved_artifacts, dict) else {}
            inner_err = str(ra.get("error") or "").strip()
            if status == "SKIPPED_EXECUTOR_DISABLED":
                hint = f"系统提示：{call_ctx.skill_id} 工具所在执行器当前处于禁用或未启动状态，重复调用不会产生任何新结果，请更换信息收集或攻击策略，不要再次选择该技能。"
            elif status and is_error_status:
                if inner_err == "MQ_POLL_TIMEOUT":
                    hint = (
                        f"系统提示：{call_ctx.skill_id} 在等待执行器/MQ Worker 写回结果时达到编排器轮询上限，"
                        f"不代表容器内工具必然失败（长任务可能仍在跑或稍后落盘）。"
                        f"请勿仅据此认定工具不可用；若 artifacts 中随后出现 SUCCESS，应以落盘结果为准。"
                    )
                else:
                    hint = f"系统提示：最近调用 {call_ctx.skill_id} 返回执行失败，可能是环境配置或目标不可达问题，请避免继续选择该工具，优先尝试同阶段的其它技能或推进到下一阶段。"
            elif file_missing:
                ws = (os.getenv("WORKSPACE_ROOT") or "/data/workspace").strip()
                hint = (
                    f"系统提示：{call_ctx.skill_id} 返回了 artifact_ref，但编排器未读到对应文件（已重试读盘）。"
                    f"请检查 orchestrator 与 executor/mq-worker 是否共用同一 WORKSPACE_ROOT 挂载（当前 {ws}），"
                    f"以及 MQ 回写是否早于读盘。"
                )
            else:
                hint = f"系统提示：{call_ctx.skill_id} 本次执行未产生可用的结构化输出，请检查工具配置或切换其他技能。"
            merged: dict[str, Any] = dict(ra) if ra else {}
            merged["error_status"] = status or "unknown"
            merged["system_error_hint"] = hint
            merged["structured_error"] = structured_error_for_skill_dispatch(
                status=status or "",
                skill_id=call_ctx.skill_id or "",
                inner_error=inner_err or None,
                file_missing=file_missing,
            )
            merged["correlation"] = correlation_dict(
                call_ctx.task_id,
                request_id=call_ctx.request_id,
                plan_id=getattr(call_ctx, "plan_id", None),
            )
            if artifact_ref:
                merged["_artifact_ref"] = artifact_ref
            resolved_artifacts = merged
            logger.debug(
                "skill_execute injected error hint skill_id=%s status=%s",
                call_ctx.skill_id, status,
            )

        top_inc = getattr(exec_result, "incremental_artifacts", None)
        if isinstance(resolved_artifacts, dict) and isinstance(top_inc, list) and top_inc:
            ra = dict(resolved_artifacts)
            if not isinstance(ra.get("incremental_artifacts"), list) or not ra.get("incremental_artifacts"):
                ra["incremental_artifacts"] = list(top_inc)[:50]
            resolved_artifacts = ra

        # dispatcher prepare 缓存写入：仅在"真实执行"（非缓存命中）且 SUCCESS 时持久化，
        # 缓存 key 已在分派前计算；未命中时 _dispatcher_cache_key 会是 None。
        if (
            not _dispatcher_cache_hit
            and _dispatcher_cache_key is not None
            and exec_result is not None
        ):
            _store_dispatcher_prepare_cache(
                state,
                _dispatcher_cache_key,
                artifact_ref=artifact_ref,
                resolved_artifacts=resolved_artifacts if isinstance(resolved_artifacts, dict) else {},
                exec_status=(exec_result.status or ""),
            )

        return {
            "exec_result": exec_result,
            "resolved_artifacts": resolved_artifacts,
            "artifact_dir": "",
        }

    async def apply_execution_result(
        self,
        state: TaskState,
        call_ctx: SkillCallContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        将单次执行结果写回 state（target_context、history_summary、put_context、workspace）。
        对写入上下文与摘要做截断，防止 Context Window 爆炸。
        返回 payload，且 payload["artifact_dir"] 会由本方法写入 artifact 后填充，供上层发 SKILL_COMPLETED 使用。
        """
        resolved_artifacts = dict(payload.get("resolved_artifacts") or {})
        exec_result = payload.get("exec_result")
        if exec_result is not None:
            inc_items = getattr(exec_result, "incremental_artifacts", None)
            if isinstance(inc_items, list) and inc_items:
                try:
                    from datetime import datetime

                    from app.clients.trace_client import emit_trace
                    from app.models import TraceEvent

                    for rec in inc_items[:25]:
                        if not isinstance(rec, dict):
                            continue
                        pl: dict[str, Any] = {
                            k: rec.get(k)
                            for k in ("kind", "summary", "severity", "ref_id", "tool")
                            if rec.get(k) is not None
                        }
                        pl["skill_id"] = call_ctx.skill_id
                        pl["target"] = (call_ctx.target or "")[:512]
                        if call_ctx.todo_id is not None:
                            pl["todo_id"] = call_ctx.todo_id
                        attach_correlation(
                            pl,
                            call_ctx.task_id,
                            request_id=call_ctx.request_id,
                            plan_id=getattr(call_ctx, "plan_id", None),
                        )
                        await emit_trace(
                            TraceEvent(
                                task_id=call_ctx.task_id,
                                timestamp=datetime.utcnow().isoformat() + "Z",
                                event_type="INCREMENTAL_ARTIFACT",
                                source_module="orchestrator",
                                payload=pl,
                            )
                        )
                except Exception:
                    pass
                tc = state.target_context if isinstance(state.target_context, dict) else {}
                bucket = tc.get("_tactical_incremental_artifacts")
                if not isinstance(bucket, list):
                    bucket = []
                for rec in inc_items[:25]:
                    if not isinstance(rec, dict) or not rec:
                        continue
                    row = {
                        k: rec.get(k)
                        for k in ("kind", "summary", "severity", "ref_id", "tool")
                        if rec.get(k) is not None
                    }
                    row["skill_id"] = call_ctx.skill_id
                    if call_ctx.todo_id is not None:
                        row["todo_id"] = call_ctx.todo_id
                    bucket.append(row)
                tc["_tactical_incremental_artifacts"] = bucket[-200:]
                state.target_context = tc

        artifact_rc = _extract_artifact_returncode(exec_result, resolved_artifacts)
        # 工具侧显式声明 rc 良性时，尊重其判断（如 ehole finger os.Exit(1) quirk）；
        # 否则非 0 returncode 视为 fatal，覆盖掉可能被 executor 错报的 SUCCESS 状态。
        rc_is_benign = _has_benign_returncode_marker(resolved_artifacts)
        has_fatal_returncode = (
            artifact_rc is not None and artifact_rc != 0 and not rc_is_benign
        )
        if exec_result is not None and has_fatal_returncode:
            # 避免执行器外层 status 误报 SUCCESS 时污染后续流程。
            try:
                exec_result.status = "FAILED"
            except Exception:
                pass
            resolved_artifacts["status_masked_by_returncode"] = True
            resolved_artifacts["effective_returncode"] = artifact_rc
        if (call_ctx.skill_id or "").lower() == "katana" and exec_result:
            st = (str(exec_result.status or "")).upper()
            if st in ("TIMEOUT", "FAILED"):
                seeds = extract_http_enum_seed_urls(state.target_context or {})
                if seeds:
                    resolved_artifacts["discovery_fallback_seed_urls"] = seeds[:24]
                    resolved_artifacts["system_recovery_hint"] = (
                        "Katana 未完成，但 http-enum 已提供表单种子 URL；"
                        "请继续执行 dispatcher（prepare）与 nuclei，种子已自动注入执行上下文。"
                    )
        truncated = _truncate_artifacts_for_context(resolved_artifacts)
        if truncated:
            # 默认关闭旧 flat key 路径，避免继续膨胀与覆盖污染；必要时可开兼容开关回滚
            if _ENABLE_FLAT_CONTEXT_COMPAT:
                for k, v in truncated.items():
                    state.target_context[f"{call_ctx.skill_id}_{k}"] = v

            # run_id 污染保护：
            # katana 的 run_id 是 web-vuln 流水线的金标（指向真实落盘的 discovery/katana_urls.txt）；
            # 后续 nuclei/dispatcher 即使内部生成新 run_id，也不应覆盖金标。
            # 已在 task-5d1c3092... 观察到：nuclei 新 run_id 覆盖 katana run_id 后，
            # 下游 read_target_list 读到错误的 web-vuln/<wrong_run_id>/discovery/ 空目录。
            sid_lower = (call_ctx.skill_id or "").strip().lower()
            if sid_lower == "katana":
                # katana 成功产出时，把其 run_id 额外 pin 到受保护的 web_vuln_run_id 键
                katana_run_id = str((resolved_artifacts or {}).get("run_id") or "").strip()
                if katana_run_id:
                    state.target_context["web_vuln_run_id"] = katana_run_id
                    logger.info(
                        "pinned katana run_id to web_vuln_run_id task_id=%s run_id=%s",
                        call_ctx.task_id, katana_run_id,
                    )
            else:
                # 非 katana 工具的 run_id 不得覆盖已存在的 run_id（若 katana 已经写过）。
                # 合规做法：从 truncated 中剔除 run_id，保留 target_context 中原有值。
                if "run_id" in truncated and state.target_context.get("run_id"):
                    truncated = {k: v for k, v in truncated.items() if k != "run_id"}

            state.target_context.update(truncated)
            _persist_namespaced_context_keys(
                state.target_context,
                skill_id=call_ctx.skill_id or "",
                artifacts=truncated,
            )
        if (call_ctx.skill_id or "").strip().lower() == "nuclei":
            _backflow_nuclei_confirmed_facts(state, resolved_artifacts or {})
            _backflow_high_value_facts(state, resolved_artifacts or {})
            # 高危扫描结果：提取 high/critical 严重级别，升级为 Tier1 事实，防止在上下文截断中丢失
            try:
                # 优先使用 nuclei 侧已构建的 llm_ready 视图；否则回退原始结构
                src = {}
                llm_ready = (resolved_artifacts or {}).get("llm_ready")
                if isinstance(llm_ready, dict):
                    src = llm_ready
                else:
                    src = resolved_artifacts or {}
                vulns = src.get("vulnerabilities") or []
                if isinstance(vulns, list) and vulns:
                    grouped: dict[str, set[str]] = {}
                    for item in vulns:
                        if not isinstance(item, dict):
                            continue
                        sev = str(item.get("severity") or "").strip().lower()
                        if sev not in ("critical", "high"):
                            continue
                        tid = str(
                            item.get("template_id")
                            or item.get("template")
                            or (item.get("info") or {}).get("name")
                            or ""
                        ).strip() or "unknown_template"
                        url = str(item.get("url") or "").strip()
                        if not url:
                            continue
                        grouped.setdefault(tid, set()).add(url)
                    for tid, urls in grouped.items():
                        urls_list = sorted(urls)
                        if not urls_list:
                            continue
                        fact = f"[Scanner_Unverified] {tid} detected on: {', '.join(urls_list)}"
                        add_tier1_fact(state, fact)
            except Exception:
                # 升级失败不应影响主流程
                pass

        # v1：结构化记忆更新（黑板 + 动作账本）
        skip_blackboard_update = bool(has_fatal_returncode)
        if not skip_blackboard_update:
            update_entity_blackboard(
                state,
                skill_id=call_ctx.skill_id,
                target=call_ctx.target,
                resolved_artifacts=resolved_artifacts or {},
            )
        # 事件驱动 baseline：基于本次结果中已确认的 http 服务进行探测，而非盲目按 target 尝试
        baseline_targets = infer_http_probe_targets(
            task_target=state.target,
            skill_id=call_ctx.skill_id,
            target=call_ctx.target,
            resolved_artifacts=resolved_artifacts or {},
        )
        baseline = None
        for bt in baseline_targets:
            baseline = await ensure_http_fallback_baseline(state, bt)
        if baseline and isinstance(truncated, dict):
            content_hash = str((truncated.get("content_hash") or "")).strip()
            if not content_hash:
                body = str(truncated.get("response_body") or truncated.get("raw_preview") or "")
                if body:
                    import hashlib
                    content_hash = hashlib.sha256(body[:4000].encode("utf-8", errors="ignore")).hexdigest()
            if content_hash:
                alias = classify_endpoint_with_baseline(state, call_ctx.target, content_hash)
                if alias.get("is_fallback_alias") and not is_crawler_confirmed_url(
                    state.target_context, call_ctx.target
                ):
                    truncated["fallback_alias"] = True
                    resolved_artifacts["fallback_alias"] = True
                    resolved_artifacts["fallback_baseline_slot"] = alias.get("baseline_slot")
                    resolved_artifacts["fallback_alias_tag"] = "[FALLBACK_ALIAS_DETECTED]"

        ledger_row = {}
        if not skip_blackboard_update:
            ledger_row = append_action_ledger(
                state,
                skill_id=call_ctx.skill_id,
                target=call_ctx.target,
                params=call_ctx.params or {},
                exec_status=(exec_result.status if exec_result else "unknown"),
                resolved_artifacts=resolved_artifacts or {},
            )
        step_line = (
            f"[{call_ctx.skill_id}] target={call_ctx.target}, status={exec_result.status if exec_result else 'unknown'}, "
            f"sig={ledger_row.get('action_signature','')[:10]}, params={json_dumps_safe(ledger_row.get('canonical_params') or {}, limit=320)}"
        )
        rid = str(call_ctx.request_id or (exec_result.request_id if exec_result else "") or "").strip()
        if rid:
            step_line += f", request_id={rid}"
        if resolved_artifacts:
            step_line += f", keys={list((resolved_artifacts or {}).keys())[:20]}"
        if (resolved_artifacts or {}).get("fallback_alias"):
            step_line += ", [FALLBACK_ALIAS_DETECTED]"
        artifact_ref = str(resolved_artifacts.get("_artifact_ref") or "")
        if artifact_ref:
            step_line += f", artifact_ref={artifact_ref}"
        state.history_summary = (state.history_summary or "").strip() + "\n" + step_line

        # 为 read_workspace_artifact 的 auto-inject 保留最近一次"生产性"工具的 artifact_ref：
        # 排除 read_* 类消费性工具，避免 read_workspace_artifact 递归引用自己。
        # 已在 task-22042fc6... 观察到：read_workspace_artifact 以空 artifact_ref 调用失败后
        # LLM 被迫退回 THREAT_MODEL 做二次 recon，浪费约 2 分钟。
        if artifact_ref:
            sid_lower = (call_ctx.skill_id or "").strip().lower()
            exec_status_lower = (exec_result.status or "").strip().lower() if exec_result else ""
            if (
                sid_lower not in ("read_workspace_artifact", "read_target_list")
                and exec_status_lower in _NON_ERROR_STATUSES
            ):
                state.target_context["_last_productive_artifact_ref"] = artifact_ref
                state.target_context["_last_productive_skill_id"] = sid_lower
                # 新的 productive artifact 出现后，解除 auto-inject 重入锁，
                # 允许 LLM 再次用 "读最新" 语义（空 ref）读取新结果；仅防重复读同一 ref。
                if state.target_context.get("_last_auto_injected_read_ref") != artifact_ref:
                    state.target_context.pop("_last_auto_injected_read_ref", None)

        ctx_to_put = _truncate_context_for_put(build_context_snapshot_for_put(state))
        await put_context(call_ctx.task_id, ctx_to_put)
        try:
            write_task_context(call_ctx.task_id, state.target_context)
        except Exception:
            pass
        summary = json_dumps_safe(resolved_artifacts, limit=10000)
        await put_artifacts_summary(call_ctx.task_id, call_ctx.skill_id, summary)

        event_id = get_event_id_from_artifact_ref(artifact_ref) if artifact_ref else ""
        memory_event_id = _resolve_memory_event_id(
            artifact_ref=artifact_ref,
            call_ctx_request_id=call_ctx.request_id,
            exec_result_request_id=(exec_result.request_id if exec_result else None),
        )
        memory_path = ""
        try:
            memory_path = write_memory_parsed_artifact(call_ctx.task_id, memory_event_id, resolved_artifacts or {})
        except Exception:
            pass
        if memory_path:
            state.target_context["last_memory_path"] = memory_path
            resolved_artifacts["_memory_path"] = memory_path

        # KB 经验候选构建 + 门槛写入（在成功执行后沉淀可回溯经验）
        try:
            from app.clients.kb_client import ExperienceCandidate, get_kb_client, get_kb_config
            from app.core.chunk_store import ChunkStoreError, write_chunk
            from app.kb_experience_payload import (
                build_intent_embed_text,
                pick_workspace_scope_from_context,
                stable_experience_artifact_chunk_id,
            )

            kb_cfg = get_kb_config()
            if kb_cfg.enabled and artifact_ref and exec_result:
                exec_status = (str(exec_result.status or "")).strip().upper()
                is_success = exec_status in ("OK", "SUCCESS")

                # 从上下文抽取少量高价值结构快照（用于后续过滤/解释）
                priority_substrings = (
                    "vuln",
                    "cve",
                    "port",
                    "service",
                    "summary",
                    "critical",
                    "high",
                    "exploit",
                    "banner",
                    "open_ports",
                    "target",
                )
                context_snapshot: dict[str, Any] = {}
                for k, v in (state.target_context or {}).items():
                    if not isinstance(k, str):
                        continue
                    kl = k.lower()
                    if not any(s in kl for s in priority_substrings):
                        continue
                    # 限制 value 大小，避免 embedding 输入过长
                    if isinstance(v, str):
                        context_snapshot[k] = v[:600]
                    elif isinstance(v, (int, float, bool)) or v is None:
                        context_snapshot[k] = v
                    else:
                        try:
                            context_snapshot[k] = json.dumps(v, ensure_ascii=False)[:600]
                        except Exception:
                            context_snapshot[k] = str(v)[:600]
                    if len(context_snapshot) >= 25:
                        break

                # 用“执行摘要 + 结构化 artifacts”构造 embedding 文本
                summary_text = (step_line + "\n" + json_dumps_safe(resolved_artifacts, limit=1800)).strip()
                mem_ok = False
                if memory_path:
                    try:
                        from pathlib import Path

                        mem_ok = Path(memory_path).exists()
                    except Exception:
                        mem_ok = False

                # 完整性：至少必须有 artifact_ref 可回溯
                if is_success and not has_fatal_returncode:
                    effectiveness = "proven" if mem_ok else "pending"
                    ws_id, pr_id = pick_workspace_scope_from_context(
                        state.target_context,
                        include_env_defaults=True,
                    )
                    kf_raw = resolved_artifacts.get("kb_features")
                    kb_head = ""
                    if isinstance(kf_raw, dict):
                        kb_head = str(kf_raw.get("intent_projection") or "").strip()[:220]
                        try:
                            context_snapshot["kb_features"] = json.dumps(kf_raw, ensure_ascii=False)[:900]
                        except Exception:
                            context_snapshot["kb_features"] = str(kf_raw)[:900]
                    headline = f"{step_line}\nkb:{kb_head}" if kb_head else step_line
                    intent_embed = build_intent_embed_text(
                        phase=call_ctx.phase.value,
                        skill_id=call_ctx.skill_id,
                        target=(call_ctx.target or "")[:512],
                        headline=headline,
                    )
                    exp_chunk_id: str | None = None
                    cid_stable = stable_experience_artifact_chunk_id(artifact_ref)
                    chunk_shell: dict[str, Any] = {
                        "kb_chunk_schema": 1,
                        "artifact_ref": artifact_ref,
                        "task_id": call_ctx.task_id,
                        "phase": call_ctx.phase.value,
                        "skill_id": call_ctx.skill_id,
                        "todo_id": call_ctx.todo_id,
                        "memory_path": memory_path or None,
                    }
                    if isinstance(kf_raw, dict) and kf_raw:
                        chunk_shell["kb_features"] = kf_raw
                    for lim in (12000, 8000, 4000, 2000, 1000, 500):
                        body = {**chunk_shell, "artifacts_excerpt": json_dumps_safe(resolved_artifacts, limit=lim)}

                        def _write_exp_chunk() -> str:
                            return write_chunk(
                                call_ctx.task_id,
                                chunk_type="kb_runtime_experience",
                                body=body,
                                tenant_id=ws_id,
                                chunk_id=cid_stable,
                                retention="proven" if mem_ok else "ephemeral",
                            )

                        try:
                            exp_chunk_id = await asyncio.to_thread(_write_exp_chunk)
                            break
                        except ChunkStoreError as cse:
                            if getattr(cse, "code", "") == "CHUNK_ID_COLLISION":
                                exp_chunk_id = cid_stable
                                break
                            if getattr(cse, "code", "") == "CHUNK_BODY_TOO_LARGE":
                                continue
                            break
                        except Exception:
                            break

                    candidate = ExperienceCandidate(
                        task_id=call_ctx.task_id,
                        phase=call_ctx.phase.value,
                        skill_id=call_ctx.skill_id,
                        todo_id=call_ctx.todo_id,
                        agent_role=call_ctx.agent_role,
                        target=call_ctx.target,
                        artifact_ref=artifact_ref,
                        event_id=event_id or None,
                        memory_path=memory_path or None,
                        summary_text=summary_text,
                        context_snapshot=context_snapshot,
                        effectiveness=effectiveness,
                        workspace_id=ws_id,
                        project_id=pr_id,
                        chunk_id=exp_chunk_id,
                        intent_embed_text=intent_embed,
                    )
                    kb_client = get_kb_client()
                    # KB 门槛追踪：通过后写入经验（写入失败不阻断主流程）
                    try:
                        from datetime import datetime
                        from app.clients.trace_client import emit_trace
                        from app.models import TraceEvent

                        kb_gate_payload: dict[str, Any] = {
                            "phase": call_ctx.phase.value,
                            "skill_id": call_ctx.skill_id,
                            "todo_id": call_ctx.todo_id,
                            "artifact_ref": artifact_ref,
                            "exec_status": exec_status,
                            "mem_ok": mem_ok,
                            "effectiveness": effectiveness,
                            "workspace_id": ws_id,
                            "project_id": pr_id,
                            "chunk_id": exp_chunk_id,
                        }
                        attach_correlation(
                            kb_gate_payload,
                            call_ctx.task_id,
                            request_id=call_ctx.request_id,
                            plan_id=getattr(call_ctx, "plan_id", None),
                        )
                        await emit_trace(
                            TraceEvent(
                                task_id=call_ctx.task_id,
                                timestamp=datetime.utcnow().isoformat() + "Z",
                                event_type="KB_GATE",
                                source_module="orchestrator",
                                payload=kb_gate_payload,
                            )
                        )
                    except Exception:
                        pass

                    doc_id = await kb_client.propose_experience(candidate)

                    try:
                        from datetime import datetime
                        from app.clients.trace_client import emit_trace
                        from app.models import TraceEvent

                        kb_prop_payload: dict[str, Any] = {
                            "phase": call_ctx.phase.value,
                            "skill_id": call_ctx.skill_id,
                            "todo_id": call_ctx.todo_id,
                            "artifact_ref": artifact_ref,
                            "doc_id": doc_id,
                            "effectiveness": effectiveness,
                            "chunk_id": exp_chunk_id,
                        }
                        attach_correlation(
                            kb_prop_payload,
                            call_ctx.task_id,
                            request_id=call_ctx.request_id,
                            plan_id=getattr(call_ctx, "plan_id", None),
                        )
                        await emit_trace(
                            TraceEvent(
                                task_id=call_ctx.task_id,
                                timestamp=datetime.utcnow().isoformat() + "Z",
                                event_type="KB_PROPOSE_EXPERIENCE",
                                source_module="orchestrator",
                                payload=kb_prop_payload,
                            )
                        )
                    except Exception:
                        pass
        except Exception:
            # MVP：KB 写入不应影响主流程
            pass

        artifact_dir = artifact_ref
        if not artifact_dir and exec_result:
            try:
                req_payload: dict[str, Any] = {
                    "task_id": call_ctx.task_id,
                    "skill_id": call_ctx.skill_id,
                    "target": call_ctx.target,
                    "params": call_ctx.params,
                    "allowed_target": call_ctx.allowed_target,
                }
                if call_ctx.agent_role is not None:
                    req_payload["agent_role"] = call_ctx.agent_role
                if call_ctx.todo_id is not None:
                    req_payload["todo_id"] = call_ctx.todo_id
                if call_ctx.request_id is not None:
                    req_payload["request_id"] = call_ctx.request_id
                artifact_dir = write_artifact(
                    task_id=call_ctx.task_id,
                    phase=call_ctx.phase.value,
                    skill_id=call_ctx.skill_id,
                    request_payload=req_payload,
                    status=exec_result.status if exec_result else "",
                    duration_ms=exec_result.duration_ms if exec_result else None,
                    raw_stdout=exec_result.raw_stdout if exec_result else None,
                    raw_stderr=exec_result.raw_stderr if exec_result else None,
                    parsed_artifacts=resolved_artifacts or {},
                )
            except Exception:
                artifact_dir = ""

        payload["artifact_dir"] = artifact_dir
        payload["skip_memory_blackboard_update"] = skip_blackboard_update
        return payload

    async def execute_skill(
        self,
        state: TaskState,
        call_ctx: SkillCallContext,
        *,
        enable_executor: bool,
        available_skills: list[str],
    ) -> dict[str, Any]:
        """
        执行一次技能调用，并在成功路径下完成：
        - workspace: artifacts 与 task_context 落盘；
        - Evidence：上下文与 artifacts 摘要同步（含截断以防 Context Window 爆炸）。

        注意：不负责 Trace 事件与循环保护，由上层调用方处理。
        单步路径：dispatch_only + apply_execution_result；多步路径由 state_machine 并发 dispatch_only 后顺序 apply。
        """
        payload = await self.execute_skill_dispatch_only(
            state=state,
            call_ctx=call_ctx,
            enable_executor=enable_executor,
            available_skills=available_skills,
        )
        if not enable_executor:
            return payload
        return await self.apply_execution_result(state, call_ctx, payload)


def json_dumps_safe(obj: dict[str, Any] | None, limit: int = 4000) -> str:
    """和 state_machine 中一致的 JSON 摘要序列化逻辑（截断到 4k 字符）。"""
    import json

    try:
        return json.dumps(obj or {}, ensure_ascii=False)[:limit]
    except Exception:
        return "{}"
