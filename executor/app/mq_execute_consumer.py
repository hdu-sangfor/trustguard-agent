"""
MQ 执行任务消息的统一消费逻辑：校验 → 幂等登记 → `_execute_impl` → Redis `register_execution_finish`。

原 fast-lane（`mq_worker`）与 agent-lane 子进程分叉已收敛为**单一路径**；`mq_agent_daemon` 与本模块是唯一生产消费入口。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any

from app.core.workspace_store import read_artifact

logger = logging.getLogger(__name__)


def mq_consumer_worker_id() -> str:
    return (os.getenv("WORKER_ID_AGENT") or os.getenv("WORKER_ID") or "").strip() or socket.gethostname()


async def process_mq_execute_task_body(body: bytes, *, log_role: str = "mq_execute") -> None:
    """
    解析 MQ 消息体并执行 skill，写回 Redis。

    log_role：日志前缀（如 agent_daemon / worker），便于区分进程。
    """
    from app.schemas.mq_execute_task import (
        mq_message_to_skill_request,
        validate_mq_execute_task_message,
    )
    from app.execution_store import register_execution_finish, register_execution_start
    from app.main import _execute_impl
    from fastapi import HTTPException
    from app.worker_daemon.sniff_pool import ArtifactSniffPool

    body_preview = body[:500].decode("utf-8", errors="replace") if body else ""
    logger.info(
        "%s message received len=%s preview=%s",
        log_role,
        len(body or []),
        body_preview[:200] if body_preview else "",
    )

    try:
        payload = json.loads(body.decode("utf-8"))
        msg = validate_mq_execute_task_message(payload)
    except Exception as e:
        logger.warning(
            "%s invalid mq message: %s body_preview=%s",
            log_role,
            e,
            body_preview[:300],
            exc_info=True,
        )
        return

    request_id = msg.request_id
    task_id = msg.task_id
    todo_id = msg.todo_id
    plan_id_log = str((msg.context or {}).get("plan_id") or "").strip()
    logger.info(
        "%s processing task_id=%s request_id=%s plan_id=%s skill_id=%s target=%s params_keys=%s",
        log_role,
        task_id,
        request_id,
        plan_id_log or "-",
        msg.skill_id,
        getattr(msg, "target", ""),
        list((msg.params or {}).keys()),
    )

    try:
        ok = await register_execution_start(
            request_id=request_id,
            task_id=task_id,
            skill_id=msg.skill_id,
            todo_id=todo_id,
        )
    except Exception as e:
        logger.exception("%s register_execution_start failed request_id=%s: %s", log_role, request_id, e)
        return

    if not ok:
        logger.info(
            "%s skip skill execution request_id=%s (duplicate dispatch or terminal replay; no side effects)",
            log_role,
            request_id,
        )
        return

    req = mq_message_to_skill_request(msg)
    sniff_pool = ArtifactSniffPool(request_id=request_id)
    result = None
    try:
        result = await _execute_impl(req, artifact_sniff_pool=sniff_pool)
        status = result.status or "SUCCESS"
        artifact_ref = (result.parsed_artifacts or {}).get("artifact_ref") or ""
        logger.info(
            "%s _execute_impl returned request_id=%s status=%s artifact_ref=%s",
            log_role,
            request_id,
            status,
            bool(artifact_ref),
        )
    except HTTPException as he:
        status = "FAILED"
        artifact_ref = ""
        logger.warning(
            "%s execute http error request_id=%s status=%s detail=%s",
            log_role,
            request_id,
            getattr(he, "status_code", ""),
            getattr(he, "detail", ""),
        )
    except Exception as e:
        status = "FAILED"
        artifact_ref = ""
        logger.exception("%s execute failed request_id=%s: %s", log_role, request_id, e)
    artifact_refs_v1_finish = result.artifact_refs_v1 if result else None

    worker_id = mq_consumer_worker_id()
    if artifact_ref and (status or "").strip().upper() in ("OK", "SUCCESS"):
        max_attempts = max(1, int(os.getenv("MQ_WORKER_ARTIFACT_READ_RETRIES", "12")))
        base_wait = float(os.getenv("MQ_WORKER_ARTIFACT_READ_WAIT_SECONDS", "0.25"))
        for i in range(max_attempts):
            ap = read_artifact(artifact_ref=artifact_ref, include_raw=False)
            parsed = (ap or {}).get("parsed") if isinstance(ap, dict) else None
            if isinstance(parsed, dict) and parsed:
                break
            if i < max_attempts - 1:
                await asyncio.sleep(min(2.0, base_wait * (2**i)))
        else:
            logger.warning(
                "%s finish pending artifact still unreadable request_id=%s artifact_ref=%s attempts=%s",
                log_role,
                request_id,
                artifact_ref,
                max_attempts,
            )
    try:
        await register_execution_finish(
            request_id=request_id,
            task_id=task_id,
            status=status,
            artifact_ref=artifact_ref or None,
            artifact_refs_v1=artifact_refs_v1_finish,
            worker_id=worker_id,
        )
    except Exception as e:
        logger.exception("%s register_execution_finish failed request_id=%s (Redis?): %s", log_role, request_id, e)

    logger.info(
        "%s done worker_id=%s request_id=%s task_id=%s skill_id=%s status=%s",
        log_role,
        worker_id,
        request_id,
        task_id,
        msg.skill_id,
        status,
    )
