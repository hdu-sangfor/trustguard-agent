"""
Worker 写回执行状态到 Redis，与编排器 RedisTaskStore 共用同一 key 布局（exec:{request_id}）。
用于 MQ Worker 消费后 register_execution_start（幂等）与 register_execution_finish。

request_id 语义（与 backlog R6b 对齐）：
- 无 exec 键：unknown，Worker 可创建 RUNNING 并执行；
- 有键且 status 为 RUNNING/PENDING、无 finished_at：in_progress，仍由首个 Consumer 执行（重复投递见下）；
- 有键且 finished_at 已设或 status 为终态：terminal，**禁止再次执行 skill**，重复投递时直接跳过。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

# 与 orchestrator RedisTaskStore 的 exec 键与字段一致
EXEC_KEY_PREFIX = "exec:"

# 与 execution_dispatcher._poll_until_execution_done 的「终态」语义一致（非 RUNNING/PENDING 即视为可结束轮询）
_TERMINAL_STATUSES = frozenset(
    {
        "SUCCESS",
        "OK",
        "FAILED",
        "TIMEOUT",
        "SKIPPED_DUPLICATE_REQUEST",
    }
)

# Module-level shared client — created once, reuses connection pool across all calls.
# Lazy-initialized on first use so unit tests that never call these functions pay no cost.
_redis_client: Any = None


def _get_redis_url() -> str:
    return (os.getenv("REDIS_URL") or "redis://localhost:6379/0").strip()


def _client():
    """Return (or create) the module-level async Redis client."""
    global _redis_client
    if _redis_client is None:
        from redis import asyncio as aioredis
        _redis_client = aioredis.from_url(
            _get_redis_url(), encoding="utf-8", decode_responses=True
        )
    return _redis_client


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def execution_record_is_terminal(data: Mapping[str, Any]) -> bool:
    """根据 Redis hash 字段判断该 request_id 是否已进入终态（不再应执行 skill 副作用）。"""
    if not data:
        return False
    finished = str(data.get("finished_at") or "").strip()
    if finished:
        return True
    st = str(data.get("status") or "").strip().upper()
    if st in _TERMINAL_STATUSES:
        return True
    return False


def build_execution_finish_artifact_fields(
    artifact_ref: Optional[str],
    artifact_refs_v1: Optional[list[str]],
) -> tuple[str, str]:
    """
    V1 双字段：返回写入 Redis 的 (artifact_ref, artifact_refs_v1 JSON 数组字符串)。
    - `artifact_ref` 为空且列表非空时，回退为列表首元（v1 读单 ref 兼容）。
    - 列表去空、保序、去重（后者与 SniffPool 一致时可再收敛）。
    """
    seen: set[str] = set()
    refs: list[str] = []
    for r in artifact_refs_v1 or []:
        s = str(r).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        refs.append(s)
    primary = (artifact_ref or "").strip()
    if not primary and refs:
        primary = refs[0]
    return primary, json.dumps(refs, ensure_ascii=False)


async def register_execution_start(
    request_id: str,
    task_id: str,
    skill_id: str,
    *,
    todo_id: Optional[str] = None,
) -> bool:
    """
    登记执行开始。与编排器共用同一 Redis 键布局（exec:{request_id}）。
    - 若 key 不存在：创建 RUNNING 记录并返回 True（Worker 应执行 skill）。
    - 若 key 已存在且记录为终态（finished_at 或终态 status）：返回 False，Worker 不得再执行 skill（MQ 重复投递保护）。
    - 若 key 已存在且仍为 in_progress（如编排器预置 RUNNING）：返回 True，由当前 Worker 继续执行。
    使用 exists + hset 避免 WRONGTYPE（不可用 setnx 创建 STRING 再 hset）。
    """
    client = _client()
    key = f"{EXEC_KEY_PREFIX}{request_id}"
    exists = await client.exists(key)
    if exists:
        data = await client.hgetall(key)
        if execution_record_is_terminal(data):
            logger.info(
                "register_execution_start skip terminal request_id=%s status=%s finished_at=%s",
                request_id,
                data.get("status"),
                bool((data.get("finished_at") or "").strip()),
            )
            return False
        return True
    mapping = {
        "task_id": task_id,
        "skill_id": skill_id,
        "todo_id": todo_id or "",
        "status": "RUNNING",
        "started_at": _now_utc_iso(),
        "artifact_ref": "",
    }
    await client.hset(key, mapping=mapping)
    return True


async def register_execution_finish(
    request_id: str,
    task_id: str,
    status: str,
    *,
    artifact_ref: Optional[str] = None,
    artifact_refs_v1: Optional[list[str]] = None,
    worker_id: Optional[str] = None,
) -> None:
    """
    写回执行结束状态、artifact_ref 与 worker_id（MQ 模式下便于验证负载均衡）。
    与 orchestrator RedisTaskStore.register_execution_finish 键布局一致。
    若传 `artifact_refs_v1`，额外写入 Hash 字段 `artifact_refs_v1`（JSON 数组 UTF-8 字符串）；
    未传则不更新该字段（兼容旧 Worker）。
    """
    client = _client()
    try:
        key = f"{EXEC_KEY_PREFIX}{request_id}"
        primary, refs_json = build_execution_finish_artifact_fields(
            artifact_ref, artifact_refs_v1
        )
        mapping: dict[str, str] = {
            "status": status,
            "artifact_ref": primary,
            "finished_at": _now_utc_iso(),
        }
        if artifact_refs_v1 is not None:
            mapping["artifact_refs_v1"] = refs_json
        if worker_id is not None:
            mapping["worker_id"] = worker_id
        await client.hset(key, mapping=mapping)
        logger.debug("execution_finish written request_id=%s status=%s key=%s", request_id, status, key)
    except Exception as e:
        logger.exception("register_execution_finish failed request_id=%s (WRONGTYPE?): %s", request_id, e)
        raise
