from __future__ import annotations

"""
TaskStore 抽象与内存实现。

v1 中 TaskStore 是 Task/Todo/Execution 的单一事实来源。本文件先提供协议与内存实现，
后续可在不改调用方的前提下增加 RedisTaskStore 等持久化实现。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

from pydantic import TypeAdapter, ValidationError

from app.enums import Phase, TaskStatus

_ARTIFACT_REFS_V1_ADAPTER: TypeAdapter[list[str]] = TypeAdapter(list[str])


def parse_artifact_refs_v1_from_redis(raw: Optional[str]) -> Optional[list[str]]:
    """
    将 Exec Hash 中的 `artifact_refs_v1`（JSON 数组字符串）解析为字符串列表。
    校验失败或为空时返回 None（与 Worker 未写入该字段的读语义一致）。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return _ARTIFACT_REFS_V1_ADAPTER.validate_json(s)
    except (ValidationError, ValueError, TypeError):
        return None


@dataclass
class TaskRecord:
    task_id: str
    name: str
    target: str
    description: Optional[str] = None
    business_background: Optional[str] = None
    extra_user_requirements: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    current_phase: Phase = Phase.RECON
    # 当前阶段开始时间（UTC），与 TaskState.phase_start_at 对齐；用于断点恢复与墙钟熔断。
    phase_start_at: Optional[datetime] = None
    # 可选：本阶段墙钟上限（秒），来自策略；None 表示未配置。
    current_phase_duration_limit_sec: Optional[int] = None
    # FinOps：LLM token 与估算成本（USD），见 governance_cost.py
    llm_input_tokens_total: int = 0
    llm_output_tokens_total: int = 0
    cumulative_cost_usd: float = 0.0
    stop_requested: bool = False
    coverage_attempted: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    summary_pointers: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TodoRecord:
    todo_id: str
    task_id: str
    name: str
    target: str
    phase: str
    status: str
    description: str = ""


@dataclass
class ExecutionRecord:
    request_id: str
    task_id: str
    skill_id: str
    todo_id: Optional[str] = None
    status: str = "PENDING"  # PENDING | RUNNING | DONE | FAILED
    artifact_ref: Optional[str] = None
    artifact_refs_v1: Optional[List[str]] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    worker_id: Optional[str] = None  # MQ 模式下执行该请求的 Worker 标识，便于负载均衡验证


class TaskStore(Protocol):
    """
    Task/Todo/Execution 持久化抽象接口。
    扩展点/可替换实现：内存（InMemoryTaskStore）、Redis（RedisTaskStore），由 get_task_store_from_env() 按配置返回。
    """

    # Task 操作 ---------------------------------------------------------
    async def create_task(self, task: TaskRecord) -> None: ...

    async def get_task(self, task_id: str) -> Optional[TaskRecord]: ...

    async def update_task(self, task: TaskRecord) -> None: ...

    async def update_status_phase(
        self,
        task_id: str,
        *,
        status: Optional[TaskStatus] = None,
        current_phase: Optional[Phase] = None,
        stop_requested: Optional[bool] = None,
    ) -> Optional[TaskRecord]: ...

    async def list_tasks(
        self,
        *,
        status: Optional[List[TaskStatus]] = None,
        phase: Optional[List[Phase]] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[TaskRecord], Optional[str]]: ...

    # Todo 操作 ---------------------------------------------------------
    async def list_todos(self, task_id: str) -> List[TodoRecord]: ...

    async def upsert_todos(self, task_id: str, todos: List[TodoRecord]) -> None: ...

    async def update_todo_status(
        self,
        task_id: str,
        todo_id: str,
        status: str,
    ) -> None: ...

    # Execution 幂等 ----------------------------------------------------
    async def register_execution_start(
        self,
        request_id: str,
        task_id: str,
        skill_id: str,
        *,
        todo_id: Optional[str],
    ) -> bool: ...

    async def register_execution_finish(
        self,
        request_id: str,
        task_id: str,
        status: str,
        *,
        artifact_ref: Optional[str],
        artifact_refs_v1: Optional[List[str]] = None,
        worker_id: Optional[str] = None,
    ) -> None: ...

    async def get_execution_status(
        self,
        request_id: str,
    ) -> Optional[ExecutionRecord]: ...

    async def list_executions_for_task(
        self,
        task_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ExecutionRecord]: ...

    async def count_executions_for_task(self, task_id: str) -> int: ...

    # 任务级锁（内存实现中为最佳努力，持久化实现应提供强约束） -------------------
    async def acquire_task_lock(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> bool: ...

    async def refresh_task_lock(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> bool: ...

    async def release_task_lock(
        self,
        task_id: str,
        owner_id: str,
    ) -> None: ...

    # 单任务在途执行数（背压；与 MAX_IN_FLIGHT_SKILLS_PER_TASK 配合） ----------------
    async def try_acquire_task_inflight(self, task_id: str, limit: int) -> bool: ...

    async def release_task_inflight(self, task_id: str) -> None: ...

    # R6d：派发层按 target / workspace 维度的在途并发槽（与 inflight 相同 Lua 语义，独立 Redis 键前缀）
    async def try_acquire_rate_slot(self, scope_key: str, limit: int) -> bool: ...

    async def release_rate_slot(self, scope_key: str) -> None: ...


# Redis：原子检查 + 递增在途计数（limit<=0 时由调用方跳过，不在此处理）
_INFLIGHT_ACQUIRE_LUA = """
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
local maxv = tonumber(ARGV[1])
if maxv == nil or maxv <= 0 then return 1 end
if cur >= maxv then return 0 end
redis.call('INCR', KEYS[1])
return 1
"""
_INFLIGHT_RELEASE_LUA = """
local v = tonumber(redis.call('GET', KEYS[1]) or '0')
if v > 0 then redis.call('DECR', KEYS[1]) end
return v
"""

_RATE_SLOT_ACQUIRE_LUA = _INFLIGHT_ACQUIRE_LUA
_RATE_SLOT_RELEASE_LUA = _INFLIGHT_RELEASE_LUA


class InMemoryTaskStore(TaskStore):
    """
    仅用于当前单实例场景与测试的内存实现。

    - 不支持进程间共享；
    - 锁为最佳努力（同进程内生效）。
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._todos: Dict[str, Dict[str, TodoRecord]] = {}
        self._executions: Dict[str, ExecutionRecord] = {}
        self._locks: Dict[str, str] = {}
        self._inflight_by_task: Dict[str, int] = {}
        self._rate_slots: Dict[str, int] = {}

    # Task --------------------------------------------------------------
    async def create_task(self, task: TaskRecord) -> None:
        if task.task_id in self._tasks:
            return
        self._tasks[task.task_id] = task

    async def get_task(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    async def update_task(self, task: TaskRecord) -> None:
        task.updated_at = datetime.utcnow()
        self._tasks[task.task_id] = task

    async def update_status_phase(
        self,
        task_id: str,
        *,
        status: Optional[TaskStatus] = None,
        current_phase: Optional[Phase] = None,
        stop_requested: Optional[bool] = None,
    ) -> Optional[TaskRecord]:
        task = self._tasks.get(task_id)
        if not task:
            return None
        if status is not None:
            task.status = status
        if current_phase is not None:
            task.current_phase = current_phase
            task.phase_start_at = datetime.utcnow()
        if stop_requested is not None:
            task.stop_requested = stop_requested
        task.updated_at = datetime.utcnow()
        return task

    async def list_tasks(
        self,
        *,
        status: Optional[List[TaskStatus]] = None,
        phase: Optional[List[Phase]] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[TaskRecord], Optional[str]]:
        items = list(self._tasks.values())
        if status:
            items = [t for t in items if t.status in status]
        if phase:
            items = [t for t in items if t.current_phase in phase]
        # 简单游标：按 task_id 排序后截断
        items.sort(key=lambda t: t.created_at)
        return items[:limit], None

    # Todo --------------------------------------------------------------
    async def list_todos(self, task_id: str) -> List[TodoRecord]:
        bucket = self._todos.get(task_id) or {}
        return list(bucket.values())

    async def upsert_todos(self, task_id: str, todos: List[TodoRecord]) -> None:
        bucket = self._todos.setdefault(task_id, {})
        for t in todos:
            bucket[t.todo_id] = t

    async def update_todo_status(
        self,
        task_id: str,
        todo_id: str,
        status: str,
    ) -> None:
        bucket = self._todos.get(task_id)
        if not bucket:
            return
        todo = bucket.get(todo_id)
        if not todo:
            return
        todo.status = status

    # Execution ---------------------------------------------------------
    async def register_execution_start(
        self,
        request_id: str,
        task_id: str,
        skill_id: str,
        *,
        todo_id: Optional[str],
    ) -> bool:
        if request_id in self._executions:
            return False
        self._executions[request_id] = ExecutionRecord(
            request_id=request_id,
            task_id=task_id,
            skill_id=skill_id,
            todo_id=todo_id,
            status="RUNNING",
            started_at=datetime.utcnow(),
        )
        return True

    async def register_execution_finish(
        self,
        request_id: str,
        task_id: str,
        status: str,
        *,
        artifact_ref: Optional[str],
        artifact_refs_v1: Optional[List[str]] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        rec = self._executions.get(request_id)
        if not rec:
            rec = ExecutionRecord(
                request_id=request_id,
                task_id=task_id,
                skill_id="",
            )
            self._executions[request_id] = rec
        rec.status = status
        rec.artifact_ref = artifact_ref
        if artifact_refs_v1 is not None:
            rec.artifact_refs_v1 = list(artifact_refs_v1)
        rec.finished_at = datetime.utcnow()
        if worker_id is not None:
            rec.worker_id = worker_id

    async def get_execution_status(
        self,
        request_id: str,
    ) -> Optional[ExecutionRecord]:
        return self._executions.get(request_id)

    async def list_executions_for_task(
        self,
        task_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ExecutionRecord]:
        cap = max(1, min(int(limit), 500))
        off = max(0, min(int(offset), 10_000))
        rows = [r for r in self._executions.values() if r.task_id == task_id]
        rows.sort(key=lambda r: r.started_at or datetime.min, reverse=True)
        return rows[off : off + cap]

    async def count_executions_for_task(self, task_id: str) -> int:
        return sum(1 for r in self._executions.values() if r.task_id == task_id)

    # Locks -------------------------------------------------------------
    async def acquire_task_lock(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> bool:
        # 内存实现：简单比较并设置，不处理 ttl
        if task_id in self._locks:
            return False
        self._locks[task_id] = owner_id
        return True

    async def refresh_task_lock(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> bool:
        # 内存实现：仅检查 owner，一律返回 True
        return self._locks.get(task_id) == owner_id

    async def release_task_lock(
        self,
        task_id: str,
        owner_id: str,
    ) -> None:
        if self._locks.get(task_id) == owner_id:
            self._locks.pop(task_id, None)

    async def try_acquire_task_inflight(self, task_id: str, limit: int) -> bool:
        if limit <= 0:
            return True
        tid = (task_id or "").strip()
        if not tid:
            return True
        cur = int(self._inflight_by_task.get(tid) or 0)
        if cur >= limit:
            return False
        self._inflight_by_task[tid] = cur + 1
        return True

    async def release_task_inflight(self, task_id: str) -> None:
        tid = (task_id or "").strip()
        if not tid:
            return
        cur = int(self._inflight_by_task.get(tid) or 0)
        if cur > 0:
            self._inflight_by_task[tid] = cur - 1

    async def try_acquire_rate_slot(self, scope_key: str, limit: int) -> bool:
        if limit <= 0:
            return True
        sk = (scope_key or "").strip()
        if not sk:
            return True
        cur = int(self._rate_slots.get(sk) or 0)
        if cur >= limit:
            return False
        self._rate_slots[sk] = cur + 1
        return True

    async def release_rate_slot(self, scope_key: str) -> None:
        sk = (scope_key or "").strip()
        if not sk:
            return
        cur = int(self._rate_slots.get(sk) or 0)
        if cur > 0:
            self._rate_slots[sk] = cur - 1


def get_task_store_from_env() -> TaskStore:
    """
    根据环境变量选择 TaskStore 实现（扩展点入口）。

    - 默认使用内存实现（单实例开发/测试）；
    - 若配置 ORCH_TASK_STORE_BACKEND=redis 且 redis 连接可用，则返回 RedisTaskStore。
    扩展点/可替换实现：可在此增加其它 backend（如 DB），与 TaskStore 协议一致即可。
    """
    backend = (os.getenv("ORCH_TASK_STORE_BACKEND", "memory") or "memory").strip().lower()
    if backend != "redis":
        return InMemoryTaskStore()

    try:
        from redis import asyncio as aioredis  # type: ignore[import]
    except Exception:
        # 无 redis 依赖或导入失败时退回内存实现
        return InMemoryTaskStore()

    redis_url = os.getenv("REDIS_URL") or "redis://localhost:6379/0"
    client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    return RedisTaskStore(client)


import os


class RedisTaskStore(TaskStore):
    """
    Redis 实现的 TaskStore（v1 目标形态）。

    当前实现采用简单 Hash 结构与乐观更新，适用于多实例编排器与 Worker。
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    # Task --------------------------------------------------------------
    async def create_task(self, task: TaskRecord) -> None:
        key = f"task:{task.task_id}"
        exists = await self._client.exists(key)
        if exists:
            return
        ps = task.phase_start_at.isoformat() + "Z" if task.phase_start_at else ""
        lim = "" if task.current_phase_duration_limit_sec is None else str(int(task.current_phase_duration_limit_sec))
        payload = {
            "name": task.name,
            "target": task.target,
            "description": task.description or "",
            "business_background": task.business_background or "",
            "extra_user_requirements": task.extra_user_requirements or "",
            "status": task.status.value,
            "current_phase": task.current_phase.value,
            "phase_start_at": ps,
            "current_phase_duration_limit_sec": lim,
            "llm_input_tokens_total": str(int(task.llm_input_tokens_total or 0)),
            "llm_output_tokens_total": str(int(task.llm_output_tokens_total or 0)),
            "cumulative_cost_usd": f"{float(task.cumulative_cost_usd or 0.0):.12f}",
            "stop_requested": "0",
            "coverage_attempted_json": "[]",
            "created_at": task.created_at.isoformat() + "Z",
            "updated_at": task.updated_at.isoformat() + "Z",
        }
        await self._client.hset(key, mapping=payload)

    async def get_task(self, task_id: str) -> Optional[TaskRecord]:
        key = f"task:{task_id}"
        data = await self._client.hgetall(key)
        if not data:
            return None
        try:
            status = TaskStatus(data.get("status", TaskStatus.PENDING.value))
            phase = Phase(data.get("current_phase", Phase.RECON.value))
        except ValueError:
            status = TaskStatus.PENDING
            phase = Phase.RECON
        created_at_str = data.get("created_at")
        updated_at_str = data.get("updated_at")
        created_at = (
            datetime.fromisoformat(created_at_str.rstrip("Z")) if created_at_str else datetime.utcnow()
        )
        updated_at = (
            datetime.fromisoformat(updated_at_str.rstrip("Z")) if updated_at_str else datetime.utcnow()
        )
        import json

        coverage_raw = data.get("coverage_attempted_json") or "[]"
        try:
            coverage = json.loads(coverage_raw)
        except Exception:
            coverage = []
        ps_raw = (data.get("phase_start_at") or "").strip()
        phase_start_at: Optional[datetime] = None
        if ps_raw:
            try:
                phase_start_at = datetime.fromisoformat(ps_raw.rstrip("Z"))
            except ValueError:
                phase_start_at = None
        lim_raw = (data.get("current_phase_duration_limit_sec") or "").strip()
        dur_lim: Optional[int] = None
        if lim_raw:
            try:
                dur_lim = max(0, int(lim_raw))
            except ValueError:
                dur_lim = None
        def _int_field(key: str) -> int:
            s = (data.get(key) or "").strip()
            if not s:
                return 0
            try:
                return max(0, int(s))
            except ValueError:
                return 0

        cost_raw = (data.get("cumulative_cost_usd") or "").strip()
        cum_cost = 0.0
        if cost_raw:
            try:
                cum_cost = max(0.0, float(cost_raw))
            except ValueError:
                cum_cost = 0.0
        return TaskRecord(
            task_id=task_id,
            name=data.get("name") or "",
            target=data.get("target") or "",
            description=data.get("description") or None,
            business_background=data.get("business_background") or None,
            extra_user_requirements=data.get("extra_user_requirements") or None,
            status=status,
            current_phase=phase,
            phase_start_at=phase_start_at,
            current_phase_duration_limit_sec=dur_lim,
            llm_input_tokens_total=_int_field("llm_input_tokens_total"),
            llm_output_tokens_total=_int_field("llm_output_tokens_total"),
            cumulative_cost_usd=cum_cost,
            stop_requested=(data.get("stop_requested") == "1"),
            coverage_attempted=coverage,
            created_at=created_at,
            updated_at=updated_at,
            summary_pointers={},
        )

    async def update_task(self, task: TaskRecord) -> None:
        key = f"task:{task.task_id}"
        task.updated_at = datetime.utcnow()
        import json

        ps = task.phase_start_at.isoformat() + "Z" if task.phase_start_at else ""
        lim = "" if task.current_phase_duration_limit_sec is None else str(int(task.current_phase_duration_limit_sec))
        payload = {
            "name": task.name,
            "target": task.target,
            "description": task.description or "",
            "business_background": task.business_background or "",
            "extra_user_requirements": task.extra_user_requirements or "",
            "status": task.status.value,
            "current_phase": task.current_phase.value,
            "phase_start_at": ps,
            "current_phase_duration_limit_sec": lim,
            "llm_input_tokens_total": str(int(task.llm_input_tokens_total or 0)),
            "llm_output_tokens_total": str(int(task.llm_output_tokens_total or 0)),
            "cumulative_cost_usd": f"{float(task.cumulative_cost_usd or 0.0):.12f}",
            "stop_requested": "1" if task.stop_requested else "0",
            "coverage_attempted_json": json.dumps(task.coverage_attempted or []),
            "created_at": task.created_at.isoformat() + "Z",
            "updated_at": task.updated_at.isoformat() + "Z",
        }
        await self._client.hset(key, mapping=payload)

    async def update_status_phase(
        self,
        task_id: str,
        *,
        status: Optional[TaskStatus] = None,
        current_phase: Optional[Phase] = None,
        stop_requested: Optional[bool] = None,
    ) -> Optional[TaskRecord]:
        record = await self.get_task(task_id)
        if not record:
            return None
        if status is not None:
            record.status = status
        if current_phase is not None:
            record.current_phase = current_phase
            record.phase_start_at = datetime.utcnow()
        if stop_requested is not None:
            record.stop_requested = stop_requested
        await self.update_task(record)
        return record

    async def list_tasks(
        self,
        *,
        status: Optional[List[TaskStatus]] = None,
        phase: Optional[List[Phase]] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[TaskRecord], Optional[str]]:
        # 简化实现：SCAN 所有 task:*，按需过滤
        pattern = "task:*"
        redis_cursor = cursor or "0"
        results: List[TaskRecord] = []
        while True:
            redis_cursor, keys = await self._client.scan(redis_cursor, match=pattern, count=limit)
            for key in keys:
                task_id = key.split(":", 1)[-1]
                rec = await self.get_task(task_id)
                if not rec:
                    continue
                if status and rec.status not in status:
                    continue
                if phase and rec.current_phase not in phase:
                    continue
                results.append(rec)
                if len(results) >= limit:
                    return results, redis_cursor if redis_cursor != "0" else None
            if redis_cursor == "0":
                break
        return results, None

    # Todo --------------------------------------------------------------
    async def list_todos(self, task_id: str) -> List[TodoRecord]:
        import json

        list_key = f"task:{task_id}:todos"
        todo_ids = await self._client.smembers(list_key)
        records: List[TodoRecord] = []
        for todo_id in todo_ids:
            key = f"todo:{todo_id}"
            data = await self._client.hgetall(key)
            if not data:
                continue
            records.append(
                TodoRecord(
                    todo_id=todo_id,
                    task_id=data.get("task_id") or task_id,
                    name=data.get("name") or "",
                    target=data.get("target") or "",
                    phase=data.get("phase") or "",
                    status=data.get("status") or "",
                    description=data.get("description") or "",
                )
            )
        return records

    async def upsert_todos(self, task_id: str, todos: List[TodoRecord]) -> None:
        list_key = f"task:{task_id}:todos"
        for t in todos:
            key = f"todo:{t.todo_id}"
            await self._client.hset(
                key,
                mapping={
                    "task_id": t.task_id,
                    "name": t.name,
                    "target": t.target,
                    "phase": t.phase,
                    "status": t.status,
                    "description": t.description,
                },
            )
            await self._client.sadd(list_key, t.todo_id)

    async def update_todo_status(
        self,
        task_id: str,
        todo_id: str,
        status: str,
    ) -> None:
        key = f"todo:{todo_id}"
        exists = await self._client.exists(key)
        if not exists:
            return
        await self._client.hset(key, "status", status)

    # Execution ---------------------------------------------------------
    async def register_execution_start(
        self,
        request_id: str,
        task_id: str,
        skill_id: str,
        *,
        todo_id: Optional[str],
    ) -> bool:
        key = f"exec:{request_id}"
        exists = await self._client.exists(key)
        if exists:
            return False
        mapping = {
            "task_id": task_id,
            "skill_id": skill_id,
            "todo_id": todo_id or "",
            "status": "RUNNING",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "artifact_ref": "",
        }
        await self._client.hset(key, mapping=mapping)
        index_key = f"orch:task_exec_ids:{task_id}"
        await self._client.lpush(index_key, request_id)
        await self._client.ltrim(index_key, 0, 499)
        return True

    async def register_execution_finish(
        self,
        request_id: str,
        task_id: str,
        status: str,
        *,
        artifact_ref: Optional[str],
        artifact_refs_v1: Optional[List[str]] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        key = f"exec:{request_id}"
        mapping = {
            "status": status,
            "artifact_ref": artifact_ref or "",
            "finished_at": datetime.utcnow().isoformat() + "Z",
        }
        if worker_id is not None:
            mapping["worker_id"] = worker_id
        if artifact_refs_v1 is not None:
            mapping["artifact_refs_v1"] = json.dumps(artifact_refs_v1, ensure_ascii=False)
        await self._client.hset(key, mapping=mapping)

    async def get_execution_status(
        self,
        request_id: str,
    ) -> Optional[ExecutionRecord]:
        key = f"exec:{request_id}"
        data = await self._client.hgetall(key)
        if not data:
            return None
        started_at_str = data.get("started_at")
        finished_at_str = data.get("finished_at")
        started_at = (
            datetime.fromisoformat(started_at_str.rstrip("Z")) if started_at_str else None
        )
        finished_at = (
            datetime.fromisoformat(finished_at_str.rstrip("Z")) if finished_at_str else None
        )
        v1_raw = data.get("artifact_refs_v1")
        artifact_refs_v1 = (
            parse_artifact_refs_v1_from_redis(v1_raw) if v1_raw is not None else None
        )
        return ExecutionRecord(
            request_id=request_id,
            task_id=data.get("task_id") or "",
            skill_id=data.get("skill_id") or "",
            todo_id=data.get("todo_id") or None,
            status=data.get("status") or "",
            worker_id=data.get("worker_id") or None,
            artifact_ref=data.get("artifact_ref") or None,
            artifact_refs_v1=artifact_refs_v1,
            started_at=started_at,
            finished_at=finished_at,
        )

    async def list_executions_for_task(
        self,
        task_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ExecutionRecord]:
        cap = max(1, min(int(limit), 500))
        off = max(0, min(int(offset), 10_000))
        index_key = f"orch:task_exec_ids:{task_id}"
        end = off + cap - 1
        ids = await self._client.lrange(index_key, off, end)
        out: List[ExecutionRecord] = []
        for rid in ids or []:
            rec = await self.get_execution_status(rid)
            if rec:
                out.append(rec)
        return out

    async def count_executions_for_task(self, task_id: str) -> int:
        index_key = f"orch:task_exec_ids:{task_id}"
        n = await self._client.llen(index_key)
        return int(n or 0)

    # 锁 ---------------------------------------------------------------
    async def acquire_task_lock(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> bool:
        key = f"lock:task:{task_id}"
        ok = await self._client.set(key, owner_id, ex=max(ttl_seconds, 1), nx=True)
        return bool(ok)

    async def refresh_task_lock(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> bool:
        key = f"lock:task:{task_id}"
        # 简化实现：仅当持有者一致时刷新 ttl
        current = await self._client.get(key)
        if current != owner_id:
            return False
        await self._client.expire(key, max(ttl_seconds, 1))
        return True

    async def release_task_lock(
        self,
        task_id: str,
        owner_id: str,
    ) -> None:
        key = f"lock:task:{task_id}"
        current = await self._client.get(key)
        if current == owner_id:
            await self._client.delete(key)

    async def try_acquire_task_inflight(self, task_id: str, limit: int) -> bool:
        if limit <= 0:
            return True
        tid = (task_id or "").strip()
        if not tid:
            return True
        key = f"orch:inflight:{tid}"
        try:
            res = await self._client.eval(_INFLIGHT_ACQUIRE_LUA, 1, key, str(limit))
        except Exception:
            return False
        try:
            return int(res) == 1
        except (TypeError, ValueError):
            return False

    async def release_task_inflight(self, task_id: str) -> None:
        tid = (task_id or "").strip()
        if not tid:
            return
        key = f"orch:inflight:{tid}"
        try:
            await self._client.eval(_INFLIGHT_RELEASE_LUA, 1, key)
        except Exception:
            pass

    async def try_acquire_rate_slot(self, scope_key: str, limit: int) -> bool:
        if limit <= 0:
            return True
        sk = (scope_key or "").strip()
        if not sk:
            return True
        key = f"orch:rl:{sk}"
        try:
            res = await self._client.eval(_RATE_SLOT_ACQUIRE_LUA, 1, key, str(limit))
        except Exception:
            return False
        try:
            return int(res) == 1
        except (TypeError, ValueError):
            return False

    async def release_rate_slot(self, scope_key: str) -> None:
        sk = (scope_key or "").strip()
        if not sk:
            return
        key = f"orch:rl:{sk}"
        try:
            await self._client.eval(_RATE_SLOT_RELEASE_LUA, 1, key)
        except Exception:
            pass
