"""
执行派发抽象：编排器将「执行请求」派发到执行层（HTTP 或 MQ）。

r4f-b：上游调用链与 PlanItem/编译器关系见 `docs/architecture.md`（本模块实现 HTTP/MQ 双模式，不区分是否经 `compile_plan_item`）。

通过 EXECUTION_DISPATCH_MODE 切换实现：http 同步执行；mq 发布到队列后在 Dispatcher 内轮询 TaskStore 直至 Worker 写回，返回终态结果。

R6c / 多 Plan 项并发：派发前对每 task 做 `TaskStore.try_acquire_task_inflight`（Redis 为 Lua 原子比较+INCR，内存实现为进程内计数），与
`MAX_IN_FLIGHT_SKILLS_PER_TASK`（默认 1）对齐；`finally` 中 `release_task_inflight`。同一 task 上多条 Plan 项若并行调用 `dispatch`，超出上限的调用在
登记 `register_execution_start` 之前即返回背压失败，避免执行层堆积。`register_execution_start` 与 R6b 终态语义共同提供 request_id 侧去重。

R6d / 扇出保护：`dispatch_rate_limit` 在 task 在途槽之后按 **同一 target**（`DISPATCH_MAX_INFLIGHT_PER_TARGET`）与 **同一 workspace/project**
（`DISPATCH_MAX_INFLIGHT_PER_WORKSPACE`，依赖 context 中的租户键）限制全局在途数；0 表示关闭。执行完毕后在 `finally` 中释放槽位。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional  # noqa: F401 - Any used in _publish_mq_execute_task

from app.core.correlation_ids import correlation_dict, correlation_log_suffix
from app.core.execution_kind import resolve_execution_kind
from app.core.task_store import TaskStore, ExecutionRecord, get_task_store_from_env
from app.core.artifact_reader import load_parsed_from_artifact_ref
from app.models import ExecuteSkillResponse
from app.orchestrator_sli_metrics import record_skill_execution_result

logger = logging.getLogger(__name__)

MQ_POLL_TIMEOUT_SECONDS = max(10, int(os.getenv("MQ_POLL_TIMEOUT_SECONDS", "120")))
MQ_POLL_INTERVAL_SECONDS = max(1, min(30, float(os.getenv("MQ_POLL_INTERVAL_SECONDS", "2"))))

_TASK_STORE: TaskStore = get_task_store_from_env()


def mq_publish_topic_for_skill(skill_id: str) -> str:
    """
    MQ 模式下所有 skill 统一发布到 **`MQ_TOPIC_AGENT`**（默认 `execute_tasks_agent`），
    由 `mq_agent_daemon`（`mq_execute_consumer`）消费。`skill_id` 保留供调用方与日志兼容。
    """
    _ = skill_id
    return (os.getenv("MQ_TOPIC_AGENT") or "execute_tasks_agent").strip() or "execute_tasks_agent"


def v1_mq_lane_health_summary() -> dict[str, Any]:
    """
    只读健康摘要：`mq_broker_configured` 仅表示是否配置了非空 `MQ_BROKER_URL`。
    `mq_dispatch_ready`：`execution_dispatch_mode` 为 `mq` 且 broker 已配置。
    已移除 fast-lane / `V1_AGENT_LANE_MQ_ENABLED` / `MQ_AGENT_LANE_SKILL_IDS`；`mq_topic_execute` 与 `mq_topic_agent`
    同为当前消费队列名（兼容旧观测键）。
    """
    agent = (os.getenv("MQ_TOPIC_AGENT") or "execute_tasks_agent").strip() or "execute_tasks_agent"
    mode = (os.getenv("EXECUTION_DISPATCH_MODE") or "http").strip().lower()
    if mode not in ("http", "mq"):
        mode = "http"
    broker_on = bool((os.getenv("MQ_BROKER_URL") or "").strip())
    mq_dispatch_ready = mode == "mq" and broker_on
    return {
        "execution_dispatch_mode": mode,
        "mq_broker_configured": broker_on,
        "mq_dispatch_ready": mq_dispatch_ready,
        "agent_lane_mq_enabled": mq_dispatch_ready,
        "mq_topic_execute": agent,
        "mq_topic_agent": agent,
        "agent_lane_allowlist_count": 0,
        "agent_lane_routing_active": mq_dispatch_ready,
        "agent_lane_publish_ready": mq_dispatch_ready,
    }


def _derive_poll_timeout_seconds(
    skill_id: str,
    params: dict[str, Any] | None,
) -> float:
    """
    基于 skill 类型与请求 timeout 推导 MQ 轮询超时，避免长任务被过早标记 TIMEOUT。
    """
    base = float(MQ_POLL_TIMEOUT_SECONDS)
    sid = (skill_id or "").strip().lower()
    p = params or {}
    requested = 0.0
    try:
        requested = float(p.get("timeout") or 0)
    except (TypeError, ValueError):
        requested = 0.0

    # 给执行层 timeout 留缓冲，覆盖 Worker 启停、容器调度与读盘重试。
    if requested > 0:
        overhead = float(os.getenv("MQ_POLL_TIMEOUT_OVERHEAD_SECONDS", "60"))
        base = max(base, requested + overhead)

    # 宏管道默认预算明显更长，避免 120s 轮询超时误伤。
    if sid == "web-vuln-pipeline":
        base = max(base, float(os.getenv("MQ_POLL_TIMEOUT_WEB_VULN_SECONDS", "720")))
    elif sid in ("nuclei", "nikto-scan", "fenjing"):
        base = max(base, float(os.getenv("MQ_POLL_TIMEOUT_HEAVY_SECONDS", "300")))
    elif sid in ("katana", "dirsearch", "dispatcher"):
        # katana/dirsearch 常 >120s；轮询过短会返回 TIMEOUT，而 Worker 仍落盘 SUCCESS，导致「失败提示」与磁盘矛盾
        base = max(base, float(os.getenv("MQ_POLL_TIMEOUT_WEB_PIPELINE_SECONDS", "600")))
    return base


async def _load_parsed_with_retry(artifact_ref: str, attempts: int = 4, wait_seconds: float = 0.25) -> dict[str, Any]:
    """
    Worker 写回 artifact_ref 后，极端情况下可能存在极短暂文件可见性延迟。
    做小步重试，避免把可恢复的瞬时缺失误判为“无结构化输出”。
    """
    max_attempts = max(1, int(os.getenv("MQ_ARTIFACT_READ_MAX_RETRIES", str(attempts))))
    base_wait = float(os.getenv("MQ_ARTIFACT_READ_WAIT_SECONDS", str(wait_seconds)))
    for i in range(max_attempts):
        parsed = load_parsed_from_artifact_ref(artifact_ref)
        if parsed:
            return parsed
        if i < max_attempts - 1:
            await asyncio.sleep(min(2.0, base_wait * (2**i)))
    return {}


async def _poll_until_execution_done(
    request_id: str,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> Optional[ExecutionRecord]:
    timeout_seconds = timeout_seconds if timeout_seconds is not None else MQ_POLL_TIMEOUT_SECONDS
    poll_interval_seconds = poll_interval_seconds if poll_interval_seconds is not None else MQ_POLL_INTERVAL_SECONDS
    store = _TASK_STORE
    if not hasattr(store, "get_execution_status"):
        logger.warning("mq_poll skip request_id=%s: store has no get_execution_status (e.g. in-memory)", request_id)
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_status = ""
    poll_count = 0
    logger.info("mq_poll start request_id=%s timeout=%ss interval=%ss", request_id, timeout_seconds, poll_interval_seconds)
    while loop.time() < deadline:
        poll_count += 1
        rec = await store.get_execution_status(request_id)
        if rec is None:
            if poll_count <= 2 or poll_count % 15 == 0:
                logger.debug("mq_poll request_id=%s attempt=%s rec=None", request_id, poll_count)
            await asyncio.sleep(poll_interval_seconds)
            continue
        last_status = (rec.status or "").strip()
        if getattr(rec, "finished_at", None) is not None:
            logger.info("mq_poll done request_id=%s after %s attempts status=%s", request_id, poll_count, last_status)
            return rec
        if last_status and last_status.upper() not in ("RUNNING", "PENDING"):
            logger.info("mq_poll done request_id=%s after %s attempts status=%s (terminal)", request_id, poll_count, last_status)
            return rec
        if poll_count % 15 == 0:
            logger.info("mq_poll waiting request_id=%s attempt=%s status=%s", request_id, poll_count, last_status or "(empty)")
        await asyncio.sleep(poll_interval_seconds)
    logger.warning("mq_poll timeout request_id=%s after %ss (%s attempts) last_status=%s", request_id, timeout_seconds, poll_count, last_status or "(none)")
    return None


class ExecutionDispatcher(ABC):
    """
    执行派发器抽象：一次技能执行请求的派发与同步返回。
    扩展点：可替换实现（如 HttpExecutionDispatcher、MqExecutionDispatcher），由 get_execution_dispatcher() 按配置返回。
    """

    @abstractmethod
    async def dispatch(
        self,
        *,
        task_id: str,
        skill_id: str,
        target: str,
        params: dict[str, Any],
        allowed_target: str | None = None,
        context: dict[str, Any] | None = None,
        execution_kind: str | None = None,
    ) -> ExecuteSkillResponse:
        """派发执行请求并返回执行结果。execution_kind 仅支持 skill 或省略。"""
        ...


def _extract_todo_id(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    raw = context.get("todo_id")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _max_in_flight_skills_per_task() -> int:
    """0 = 不限制；>=1 时 ExecutionDispatcher 在派发前做 per-task 在途背压。"""
    try:
        return max(0, int(os.getenv("MAX_IN_FLIGHT_SKILLS_PER_TASK", "1") or "1"))
    except ValueError:
        return 1


def _inflight_backpressure_response() -> ExecuteSkillResponse:
    lim = _max_in_flight_skills_per_task()
    return ExecuteSkillResponse(
        status="FAILED",
        parsed_artifacts={
            "error": "MAX_IN_FLIGHT_SKILLS_PER_TASK",
            "message": f"max concurrent in-flight skill executions per task reached ({lim})",
            "limit": lim,
        },
        raw_stdout=None,
        raw_stderr=None,
        duration_ms=0,
    )


class HttpExecutionDispatcher(ExecutionDispatcher):
    """HTTP 实现：通过 TaskStore + /v1/execute 完成一次同步执行（含 request_id 幂等记录）。"""

    async def dispatch(
        self,
        *,
        task_id: str,
        skill_id: str,
        target: str,
        params: dict[str, Any],
        allowed_target: str | None = None,
        context: dict[str, Any] | None = None,
        execution_kind: str | None = None,
    ) -> ExecuteSkillResponse:
        from fastapi import HTTPException
        from app.clients.executor_client import call_executor
        from app.core.dispatch_rate_limit import (
            acquire_dispatch_rate_limits,
            dispatch_max_inflight_per_target,
            dispatch_max_inflight_per_workspace,
            dispatch_rate_limit_exceeded_response,
            release_dispatch_rate_limits,
        )

        lim = _max_in_flight_skills_per_task()
        inflight_acquired = False
        if lim > 0:
            ok_slot = await _TASK_STORE.try_acquire_task_inflight(task_id, lim)
            if not ok_slot:
                return _inflight_backpressure_response()
            inflight_acquired = True

        rl_releases: list = []
        if dispatch_max_inflight_per_target() > 0 or dispatch_max_inflight_per_workspace() > 0:
            ok_rl, rl_releases = await acquire_dispatch_rate_limits(
                _TASK_STORE,
                target=target,
                context=context,
            )
            if not ok_rl:
                if inflight_acquired:
                    await _TASK_STORE.release_task_inflight(task_id)
                    inflight_acquired = False
                tlim = dispatch_max_inflight_per_target()
                wlim = dispatch_max_inflight_per_workspace()
                lim_used = max((x for x in (tlim, wlim) if x > 0), default=0)
                return dispatch_rate_limit_exceeded_response(
                    error_code="DISPATCH_RATE_LIMIT",
                    limit=lim_used,
                    scope_hint="per_target_and_or_workspace",
                )

        try:
            ek = resolve_execution_kind(skill_id=skill_id, execution_kind=execution_kind)
            # 为本次执行生成全局唯一 request_id，并在 TaskStore 中登记开始记录；
            # 若登记失败（理论上极少发生），视为重复请求并直接返回占位结果，避免重复执行。
            request_id = str((context or {}).get("request_id") or "").strip() or f"req-{uuid.uuid4().hex}"
            plan_id_ctx = str((context or {}).get("plan_id") or "").strip() or None
            _corr = correlation_dict(task_id, request_id=request_id, plan_id=plan_id_ctx)
            logger.info(
                "http_dispatch skill_id=%s execution_kind=%s %s",
                skill_id,
                ek,
                correlation_log_suffix(_corr),
            )
            todo_id = _extract_todo_id(context)
            ok = await _TASK_STORE.register_execution_start(
                request_id=request_id,
                task_id=task_id,
                skill_id=skill_id,
                todo_id=todo_id,
            )
            if not ok:
                record_skill_execution_result(skill_id, "SKIPPED_DUPLICATE_REQUEST")
                return ExecuteSkillResponse(
                    status="SKIPPED_DUPLICATE_REQUEST",
                    parsed_artifacts={},
                    raw_stdout=None,
                    raw_stderr=None,
                    duration_ms=0,
                )

            exec_context = dict(context or {})
            exec_context.setdefault("request_id", request_id)
            try:
                exec_result = await call_executor(
                    task_id=task_id,
                    skill_id=skill_id,
                    target=target,
                    params=params,
                    allowed_target=allowed_target,
                    context=exec_context,
                    request_id=request_id,
                    execution_kind=ek,
                )
            except HTTPException:
                await _TASK_STORE.register_execution_finish(
                    request_id=request_id,
                    task_id=task_id,
                    status="FAILED",
                    artifact_ref=None,
                )
                record_skill_execution_result(skill_id, "FAILED")
                raise
            except Exception:
                await _TASK_STORE.register_execution_finish(
                    request_id=request_id,
                    task_id=task_id,
                    status="FAILED",
                    artifact_ref=None,
                )
                record_skill_execution_result(skill_id, "FAILED")
                raise

            artifact_ref = ""
            try:
                artifact_ref = str((exec_result.parsed_artifacts or {}).get("artifact_ref") or "")
            except Exception:
                artifact_ref = ""

            await _TASK_STORE.register_execution_finish(
                request_id=request_id,
                task_id=task_id,
                status=exec_result.status,
                artifact_ref=artifact_ref or None,
            )
            record_skill_execution_result(skill_id, exec_result.status)
            return exec_result.model_copy(update={"request_id": request_id})
        finally:
            await release_dispatch_rate_limits(rl_releases)
            if inflight_acquired:
                await _TASK_STORE.release_task_inflight(task_id)


def _get_mq_publisher():
    """
    根据 MQ_BACKEND / MQ_BROKER_URL 返回可用 MQ 发布者；未配置时返回 None（走 stub 日志）。
    扩展点/可替换实现：可在此或通过 MQ_PUBLISHER 注入接入 Kafka 等其它 backend。
    """
    broker_url = (os.getenv("MQ_BROKER_URL") or "").strip()
    if not broker_url:
        return None
    backend = (os.getenv("MQ_BACKEND", "rabbitmq") or "rabbitmq").strip().lower()
    if backend == "rabbitmq":
        try:
            return _RabbitMQPublisher(broker_url)
        except Exception as e:
            logger.warning("mq_publisher init failed (broker_url=%s): %s", broker_url[:50], e)
            return None
    logger.warning("unsupported MQ_BACKEND=%s", backend)
    return None


class _RabbitMQPublisher:
    """RabbitMQ 发布者：将消息发往指定 queue（由调用方传入 topic，通常为 MQ_TOPIC_AGENT）。"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._conn: Any = None
        self._channel: Any = None

    def _ensure_connection(self) -> None:
        import pika
        if self._channel and self._conn and self._conn.is_open:
            return
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = pika.BlockingConnection(pika.URLParameters(self._url))
        self._channel = self._conn.channel()

    def publish(self, topic: str, body: bytes) -> None:
        """同步发布到 queue=topic。body 为已序列化的消息体（通常 JSON bytes）。"""
        import pika
        self._ensure_connection()
        self._channel.queue_declare(queue=topic, durable=True)
        self._channel.basic_publish(
            exchange="",
            routing_key=topic,
            body=body,
            properties=pika.BasicProperties(delivery_mode=2),
        )

    def close(self) -> None:
        try:
            if self._channel:
                self._channel.close()
            if self._conn and self._conn.is_open:
                self._conn.close()
        except Exception:
            pass
        self._channel = None
        self._conn = None


def _publish_mq_execute_task(msg: Any) -> None:
    """
    将 mq_execute_task 消息序列化后发布到 MQ 队列（单队列 agent 车道，见 `mq_publish_topic_for_skill`）。
    未配置 MQ_BROKER_URL 时仅打日志（stub）；EXECUTION_DISPATCH_MODE=mq 且配置了 broker 时真正写入队列。
    扩展点/可替换实现：发布逻辑可替换为其它 MQ 或通道，与 _get_mq_publisher 配合。
    """
    skill_id = str(getattr(msg, "skill_id", "") or "")
    topic = mq_publish_topic_for_skill(skill_id)
    try:
        publisher = _get_mq_publisher()
        if publisher:
            payload = msg.model_dump() if hasattr(msg, "model_dump") else msg
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            logger.info(
                "mq_publish serialized request_id=%s skill_id=%s lane_topic=%s body_len=%s params_keys=%s",
                getattr(msg, "request_id", ""),
                skill_id,
                topic,
                len(body),
                list((payload.get("params") or {}).keys()),
            )
            publisher.publish(topic, body)
            logger.info(
                "mq_publish ok topic=%s request_id=%s task_id=%s skill_id=%s",
                topic, getattr(msg, "request_id", ""), getattr(msg, "task_id", ""), skill_id,
            )
        else:
            logger.info(
                "mq_publish (no broker) topic=%s request_id=%s task_id=%s skill_id=%s",
                topic, getattr(msg, "request_id", ""), getattr(msg, "task_id", ""), skill_id,
            )
    except (TypeError, ValueError) as e:
        logger.exception("mq_publish serialize failed (params/JSON?): %s msg.params=%s", e, getattr(msg, "params", None))
        raise
    except Exception as e:
        logger.warning("mq_publish failed: %s", e)


class MqExecutionDispatcher(ExecutionDispatcher):
    """
    MQ 实现：登记 request_id 后发布 mq_execute_task 消息，在 dispatch 内轮询 TaskStore 直至 Worker 写回，
    返回 SUCCESS/FAILED/TIMEOUT 等终态及 parsed_artifacts，状态机不再处理 DISPATCHED。
    """

    async def dispatch(
        self,
        *,
        task_id: str,
        skill_id: str,
        target: str,
        params: dict[str, Any],
        allowed_target: str | None = None,
        context: dict[str, Any] | None = None,
        execution_kind: str | None = None,
    ) -> ExecuteSkillResponse:
        from app.schemas.mq_execute_task import build_mq_execute_task_message
        from app.core.dispatch_rate_limit import (
            acquire_dispatch_rate_limits,
            dispatch_max_inflight_per_target,
            dispatch_max_inflight_per_workspace,
            dispatch_rate_limit_exceeded_response,
            release_dispatch_rate_limits,
        )

        allowed = (allowed_target or "").strip()
        if not allowed:
            return ExecuteSkillResponse(
                status="FAILED",
                parsed_artifacts={},
                raw_stdout=None,
                raw_stderr=None,
                duration_ms=0,
            )

        lim = _max_in_flight_skills_per_task()
        inflight_acquired = False
        if lim > 0:
            ok_slot = await _TASK_STORE.try_acquire_task_inflight(task_id, lim)
            if not ok_slot:
                return _inflight_backpressure_response()
            inflight_acquired = True

        rl_releases: list = []
        if dispatch_max_inflight_per_target() > 0 or dispatch_max_inflight_per_workspace() > 0:
            ok_rl, rl_releases = await acquire_dispatch_rate_limits(
                _TASK_STORE,
                target=target,
                context=context,
            )
            if not ok_rl:
                if inflight_acquired:
                    await _TASK_STORE.release_task_inflight(task_id)
                    inflight_acquired = False
                tlim = dispatch_max_inflight_per_target()
                wlim = dispatch_max_inflight_per_workspace()
                lim_used = max((x for x in (tlim, wlim) if x > 0), default=0)
                return dispatch_rate_limit_exceeded_response(
                    error_code="DISPATCH_RATE_LIMIT",
                    limit=lim_used,
                    scope_hint="per_target_and_or_workspace",
                )

        try:
            ek = resolve_execution_kind(skill_id=skill_id, execution_kind=execution_kind)
            request_id = str((context or {}).get("request_id") or "").strip() or f"req-{uuid.uuid4().hex}"
            plan_id_ctx = str((context or {}).get("plan_id") or "").strip() or None
            _corr_mq = correlation_dict(task_id, request_id=request_id, plan_id=plan_id_ctx)
            logger.info(
                "mq_dispatch publish skill_id=%s execution_kind=%s %s",
                skill_id,
                ek,
                correlation_log_suffix(_corr_mq),
            )
            todo_id = _extract_todo_id(context)
            ok = await _TASK_STORE.register_execution_start(
                request_id=request_id,
                task_id=task_id,
                skill_id=skill_id,
                todo_id=todo_id,
            )
            if not ok:
                record_skill_execution_result(skill_id, "SKIPPED_DUPLICATE_REQUEST")
                return ExecuteSkillResponse(
                    status="SKIPPED_DUPLICATE_REQUEST",
                    parsed_artifacts={},
                    raw_stdout=None,
                    raw_stderr=None,
                    duration_ms=0,
                )

            exec_ctx = dict(context or {})
            exec_ctx.setdefault("request_id", request_id)
            msg = build_mq_execute_task_message(
                request_id=request_id,
                task_id=task_id,
                skill_id=skill_id,
                target=target,
                params=params or {},
                allowed_target=allowed,
                context=exec_ctx,
                todo_id=todo_id,
                execution_kind=ek,
            )
            _publish_mq_execute_task(msg)
            logger.info(
                "mq_dispatch published request_id=%s task_id=%s skill_id=%s (polling for result)",
                request_id, task_id, skill_id,
            )
            poll_timeout_seconds = _derive_poll_timeout_seconds(skill_id, params)
            rec = await _poll_until_execution_done(request_id, timeout_seconds=poll_timeout_seconds)
            if rec:
                artifact_ref = rec.artifact_ref or ""
                parsed = await _load_parsed_with_retry(artifact_ref) if artifact_ref else {}
                if parsed:
                    parsed_artifacts = dict(parsed)
                else:
                    parsed_artifacts = {
                        "error": rec.status,
                        "message": "Worker finished but no artifact" if (rec.status or "").upper() not in ("OK", "SUCCESS") else "Done",
                    }
                    if getattr(rec, "worker_id", None):
                        parsed_artifacts["worker_id"] = rec.worker_id
                if artifact_ref:
                    parsed_artifacts["artifact_ref"] = artifact_ref
                record_skill_execution_result(skill_id, rec.status or "SUCCESS")
                _inc = parsed_artifacts.get("incremental_artifacts")
                _inc_list = _inc if isinstance(_inc, list) else None
                return ExecuteSkillResponse(
                    status=rec.status or "SUCCESS",
                    parsed_artifacts=parsed_artifacts,
                    raw_stdout=None,
                    raw_stderr=None,
                    duration_ms=0,
                    request_id=request_id,
                    incremental_artifacts=_inc_list,
                )
            record_skill_execution_result(skill_id, "TIMEOUT")
            return ExecuteSkillResponse(
                status="TIMEOUT",
                parsed_artifacts={
                    "error": "MQ_POLL_TIMEOUT",
                    "message": "Worker did not write back within timeout",
                    "poll_timeout_seconds": poll_timeout_seconds,
                    "skill_id": skill_id,
                },
                raw_stdout=None,
                raw_stderr=None,
                duration_ms=0,
                request_id=request_id,
            )
        finally:
            await release_dispatch_rate_limits(rl_releases)
            if inflight_acquired:
                await _TASK_STORE.release_task_inflight(task_id)


def get_execution_dispatcher() -> ExecutionDispatcher:
    """
    根据环境变量 EXECUTION_DISPATCH_MODE 返回当前派发实现（http | mq）。
    MQ 模式强制要求 ORCH_TASK_STORE_BACKEND=redis 与 REDIS_URL，否则轮询无法拿到 Worker 写回结果。
    """
    mode = (os.getenv("EXECUTION_DISPATCH_MODE", "http") or "http").strip().lower()
    if mode == "mq":
        backend = (os.getenv("ORCH_TASK_STORE_BACKEND") or "").strip().lower()
        redis_url = (os.getenv("REDIS_URL") or "").strip()
        if backend != "redis" or not redis_url:
            raise RuntimeError(
                "EXECUTION_DISPATCH_MODE=mq requires ORCH_TASK_STORE_BACKEND=redis and REDIS_URL to be set; "
                "otherwise the orchestrator cannot see Worker execution results."
            )
        logger.debug("execution_dispatcher mode=mq (poll until worker writes result)")
        return MqExecutionDispatcher()
    logger.debug("execution_dispatcher mode=http (sync call to executor)")
    return HttpExecutionDispatcher()
