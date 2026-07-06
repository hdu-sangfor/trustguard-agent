"""
PentestManagerAgent 与简化 TodoList 雏形。

当 ENABLE_MANAGER_AGENT=true 时生效：维护内存 + checkpoint 持久化的 TodoList，
每 tick 先根据 target_context 规则补充 Todo，再选一个 PENDING Todo 注入决策上下文，
执行后更新 Todo 状态并写回 target_context["_todos"]。
参考 Trae-Agent 的 TraeAgent 任务驱动风格，逻辑按 PTES 渗透流程重写。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List

from app.models import Phase, TaskState
from app.core.task_store import TaskStore, TodoRecord


class TodoStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass
class TodoItem:
    """单个渗透子任务（如某端口的扫描、某 URL 的 Web 测试、某高危洞的 Exploit 验证）。"""

    id: str
    name: str
    target: str
    phase: str
    status: TodoStatus
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "target": self.target,
            "phase": self.phase,
            "status": self.status.value,
            "description": self.description or "",
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TodoItem:
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            target=str(d.get("target", "")),
            phase=str(d.get("phase", "RECON")),
            status=TodoStatus(d.get("status", TodoStatus.PENDING.value)),
            description=str(d.get("description", "")),
        )


def get_todos_from_state(state: TaskState) -> List[TodoItem]:
    """从 state.target_context["_todos"] 恢复 Todo 列表。"""
    raw = (state.target_context or {}).get("_todos")
    if not raw:
        return []
    if isinstance(raw, list):
        items = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    items.append(TodoItem.from_dict(item))
                except (ValueError, KeyError):
                    continue
        return items
    return []


def persist_todos_to_state(state: TaskState, todos: List[TodoItem]) -> None:
    """将 Todo 列表写回 state.target_context["_todos"]，便于 checkpoint 持久化。"""
    if state.target_context is None:
        state.target_context = {}
    state.target_context["_todos"] = [t.to_dict() for t in todos]


def collect_suspicious_signal_items(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    从 target_context 收集所有 suspicious_signals 列表（含 dispatcher_*/web-vuln-pipeline_* 等前缀键）。
    用于信号驱动 Todo，不依赖仅 high_severity。
    """
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for k, v in (ctx or {}).items():
        if not isinstance(k, str) or "suspicious_signals" not in k.lower():
            continue
        if not isinstance(v, list):
            continue
        for item in v:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            reason = str(item.get("reason") or "")
            conf = str(item.get("confidence") or "")
            key = (url, reason, conf)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out[:50]


def collect_tech_stack_evidence_items(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """收集 Nuclei info/low 抽取的 tech_stack_evidence（各 skill 前缀键）。"""
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for k, v in (ctx or {}).items():
        if not isinstance(k, str) or "tech_stack_evidence" not in k.lower():
            continue
        if not isinstance(v, list):
            continue
        for item in v:
            if not isinstance(item, dict):
                continue
            sig = str(item.get("signal") or "")
            tid = str(item.get("template_id") or "")
            url = str(item.get("url") or "")
            sev = str(item.get("severity") or "")
            key = (sig, tid, url, sev)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out[:80]


def _host_port_from_target(base_target: str, port: int) -> str:
    """从任务 target 与端口构造 URL。"""
    base = (base_target or "").strip()
    if not base:
        return f"http://127.0.0.1:{port}"
    if "://" in base:
        prefix = base.split("://")[0]
        rest = base.split("//", 1)[-1].split("/")[0].rstrip("/")
        if ":" in rest:
            rest = rest.rsplit(":", 1)[0]
        return f"{prefix}://{rest}:{port}"
    return f"http://{base}:{port}" if ":" not in base else f"http://{base}:{port}"


def ensure_todos_from_context(state: TaskState) -> None:
    """
    根据 target_context / 当前 phase 用简单规则补充 Todo，避免明显漏扫。
    - 发现新服务/端口：补充对应 VULN_SCAN Todo；
    - 信号驱动：dispatcher 的 suspicious_signals、Nuclei info/low 的 tech_stack_evidence（疑罪从有，人工复核）；
    - EXPLOIT 阶段：high_severity / critical_findings，以及技术栈与 ETL 可疑信号跟进 Todo。
    """
    todos = get_todos_from_state(state)
    seen: Dict[str, bool] = {f"{t.phase}:{t.target}:{t.name}": True for t in todos}

    # 从 target_context 提取端口/服务（简单启发）
    ctx = state.target_context or {}
    open_ports = ctx.get("open_ports") or ctx.get("ports") or []
    if isinstance(open_ports, dict):
        open_ports = list(open_ports.keys()) if open_ports else []
    elif not isinstance(open_ports, list):
        open_ports = []
    services = ctx.get("services") or ctx.get("discovered_services") or []
    if not isinstance(services, list):
        services = []

    # 为每个端口/服务补充 VULN_SCAN Todo（若尚未存在）
    base_target = state.target
    for port in open_ports[:20]:  # 限制数量
        if isinstance(port, int):
            target = _host_port_from_target(base_target, port)
        else:
            target = str(port) if "://" in str(port) else f"{base_target.rstrip('/')}:{port}"
        key = f"VULN_SCAN:{target}:scan"
        if key not in seen:
            seen[key] = True
            todos.append(
                TodoItem(
                    id=f"todo-{uuid.uuid4().hex[:8]}",
                    name="VULN_SCAN",
                    target=target,
                    phase="VULN_SCAN",
                    status=TodoStatus.PENDING,
                    description=f"Vulnerability scan for {target}",
                )
            )
    for svc in services[:15]:
        if isinstance(svc, dict):
            tgt = svc.get("target") or svc.get("url") or state.target
            name = svc.get("name") or "VULN_SCAN"
        else:
            tgt = state.target
            name = "VULN_SCAN"
        key = f"VULN_SCAN:{tgt}:{name}"
        if key not in seen:
            seen[key] = True
            todos.append(
                TodoItem(
                    id=f"todo-{uuid.uuid4().hex[:8]}",
                    name=name,
                    target=str(tgt),
                    phase="VULN_SCAN",
                    status=TodoStatus.PENDING,
                    description=f"Scan/service check for {tgt}",
                )
            )

    # 信号驱动：ETL suspicious_signals（与 high_severity 独立）；仅在扫描相关阶段补充 VULN_SCAN Todo
    susp_items = collect_suspicious_signal_items(ctx)
    tech_items = collect_tech_stack_evidence_items(ctx)
    scan_phases = (Phase.RECON, Phase.THREAT_MODEL, Phase.VULN_SCAN)
    if state.current_phase in scan_phases and susp_items:
        key = f"VULN_SCAN:{base_target}:etl_suspicious_signals"
        if key not in seen:
            seen[key] = True
            preview = "; ".join(
                f"{str(s.get('url') or '')[:48]}({s.get('reason')})" for s in susp_items[:5]
            )
            todos.append(
                TodoItem(
                    id=f"todo-{uuid.uuid4().hex[:8]}",
                    name="VULN_SCAN",
                    target=base_target,
                    phase="VULN_SCAN",
                    status=TodoStatus.PENDING,
                    description=f"[signals] ETL suspicious_signals n={len(susp_items)}: {preview}",
                )
            )

    if state.current_phase in scan_phases and tech_items:
        key = f"VULN_SCAN:{base_target}:nuclei_tech_stack_evidence"
        if key not in seen:
            seen[key] = True
            sigs = sorted({str(t.get("signal") or "") for t in tech_items if t.get("signal")})[:15]
            todos.append(
                TodoItem(
                    id=f"todo-{uuid.uuid4().hex[:8]}",
                    name="VULN_SCAN",
                    target=base_target,
                    phase="VULN_SCAN",
                    status=TodoStatus.PENDING,
                    description=f"[signals] Nuclei info/low tech_stack: {','.join(sigs)} (manual verify)",
                )
            )

    # EXPLOIT 阶段：若上下文中存在高危相关键，补充 EXPLOIT Todo
    if state.current_phase == Phase.EXPLOIT:
        high_severity = ctx.get("high_severity") or ctx.get("critical_findings") or []
        if not isinstance(high_severity, list):
            high_severity = [high_severity] if high_severity else []
        for item in high_severity[:10]:
            if isinstance(item, dict):
                tgt = item.get("target") or item.get("url") or state.target
                desc = item.get("description") or item.get("cve") or "High severity"
            else:
                tgt = state.target
                desc = str(item)
            key = f"EXPLOIT:{tgt}:exploit"
            if key not in seen:
                seen[key] = True
                todos.append(
                    TodoItem(
                        id=f"todo-{uuid.uuid4().hex[:8]}",
                        name="EXPLOIT",
                        target=str(tgt),
                        phase="EXPLOIT",
                        status=TodoStatus.PENDING,
                        description=desc,
                    )
                )

        risky_stack = frozenset({"struts", "spring", "jenkins", "weblogic", "jboss", "wildfly"})
        tech_hits = [t for t in tech_items if str(t.get("signal") or "").lower() in risky_stack]
        if tech_hits:
            key = f"EXPLOIT:{base_target}:tech_stack_manual"
            if key not in seen:
                seen[key] = True
                todos.append(
                    TodoItem(
                        id=f"todo-{uuid.uuid4().hex[:8]}",
                        name="EXPLOIT",
                        target=base_target,
                        phase="EXPLOIT",
                        status=TodoStatus.PENDING,
                        description="[signals] Tech stack from Nuclei info/low — manual exploit validation",
                    )
                )

        exploit_susp_n = 0
        for s in susp_items:
            if exploit_susp_n >= 5:
                break
            try:
                cf = float(s.get("confidence")) if s.get("confidence") is not None else 0.0
            except (TypeError, ValueError):
                cf = 0.0
            if cf < 0.3:
                continue
            tgt = str(s.get("url") or "").strip()
            if not tgt.startswith(("http://", "https://")):
                tgt = base_target
            dk = f"EXPLOIT:susp_etl:{tgt}:{s.get('reason')}"
            if dk in seen:
                continue
            seen[dk] = True
            exploit_susp_n += 1
            todos.append(
                TodoItem(
                    id=f"todo-{uuid.uuid4().hex[:8]}",
                    name="EXPLOIT",
                    target=tgt,
                    phase="EXPLOIT",
                    status=TodoStatus.PENDING,
                    description=f"[signals] Verify ETL suspicious: {s.get('reason')} conf={cf}",
                )
            )

    persist_todos_to_state(state, todos)


def pick_next_pending_todo(state: TaskState) -> TodoItem | None:
    """
    选取一个 PENDING 的 Todo，将其置为 RUNNING 并写回 state。
    优先选与当前 phase 一致的 Todo。
    """
    todos = get_todos_from_state(state)
    current_phase_val = state.current_phase.value if state.current_phase else ""
    # 先选同阶段
    for t in todos:
        if t.status == TodoStatus.PENDING and t.phase == current_phase_val:
            t.status = TodoStatus.RUNNING
            persist_todos_to_state(state, todos)
            return t
    # 再选任意 PENDING
    for t in todos:
        if t.status == TodoStatus.PENDING:
            t.status = TodoStatus.RUNNING
            persist_todos_to_state(state, todos)
            return t
    return None


def set_todo_status(state: TaskState, todo_id: str, status: TodoStatus) -> None:
    """将指定 id 的 Todo 设为给定状态并持久化。"""
    todos = get_todos_from_state(state)
    for t in todos:
        if t.id == todo_id:
            t.status = status
            break
    persist_todos_to_state(state, todos)


def inject_current_todo_into_context(state: TaskState, todo: TodoItem | None) -> None:
    """将当前选中的 Todo 注入 target_context，供决策引擎使用。"""
    if state.target_context is None:
        state.target_context = {}
    if todo is None:
        state.target_context.pop("_current_todo", None)
        return
    state.target_context["_current_todo"] = todo.to_dict()


def clear_current_todo_from_context(state: TaskState) -> None:
    """清除本 tick 注入的 _current_todo。"""
    if state.target_context:
        state.target_context.pop("_current_todo", None)


class PentestManagerAgent:
    """
    计划中的 PentestManagerAgent 命名入口：与 ``ENABLE_MANAGER_AGENT`` + ``tick_manager`` 路径一致。

    实现为对模块级函数的薄封装（静态方法别名），便于导入与文档索引；逻辑见各独立函数。
    """

    ensure_todos_from_context = staticmethod(ensure_todos_from_context)
    get_todos_from_state = staticmethod(get_todos_from_state)
    persist_todos_to_state = staticmethod(persist_todos_to_state)
    pick_next_pending_todo = staticmethod(pick_next_pending_todo)
    inject_current_todo_into_context = staticmethod(inject_current_todo_into_context)
    clear_current_todo_from_context = staticmethod(clear_current_todo_from_context)
    set_todo_status = staticmethod(set_todo_status)
    collect_suspicious_signal_items = staticmethod(collect_suspicious_signal_items)
    collect_tech_stack_evidence_items = staticmethod(collect_tech_stack_evidence_items)


# === TaskStore 适配层：用于 v1 下从 TaskStore 读写 Todo ===


async def load_todos_from_store(store: TaskStore, task_id: str) -> List[TodoItem]:
    """
    从 TaskStore 加载指定任务的 Todo 列表，并转换为 TodoItem。
    仅作为 ManagerAgent 的数据来源之一，v1 下应优先使用本方法而非 _todos。
    """
    records = await store.list_todos(task_id)
    items: List[TodoItem] = []
    for rec in records:
        try:
            items.append(
                TodoItem(
                    id=rec.todo_id,
                    name=rec.name,
                    target=rec.target,
                    phase=rec.phase,
                    status=TodoStatus(rec.status or TodoStatus.PENDING.value),
                    description=rec.description or "",
                )
            )
        except ValueError:
            continue
    return items


async def persist_todos_to_store(store: TaskStore, task_id: str, todos: List[TodoItem]) -> None:
    """
    将 TodoItem 列表写回 TaskStore，覆盖同一任务下的 Todo 记录。
    """
    records: List[TodoRecord] = []
    for t in todos:
        records.append(
            TodoRecord(
                todo_id=t.id,
                task_id=task_id,
                name=t.name,
                target=t.target,
                phase=t.phase,
                status=t.status.value,
                description=t.description or "",
            )
        )
    await store.upsert_todos(task_id, records)
