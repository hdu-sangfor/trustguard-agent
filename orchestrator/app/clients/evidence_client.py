"""
调用Evidence：上下文（PUT context）、证据落盘（PUT artifacts）。
编排器在每步执行成功后同步 target_context 并写入 artifacts 摘要，便于报告与续跑。
"""
import logging
import os
from typing import Any, Dict

import httpx

from app.clients.evidence_http_error import evidence_http_error_detail

EVIDENCE_BASE_URL = os.getenv("EVIDENCE_BASE_URL", "http://localhost:18103")

_log = logging.getLogger(__name__)


async def put_context(task_id: str, context: Dict[str, Any]) -> None:
    """将当前 target_context 同步到Evidence，供查询与断点恢复一致。"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{EVIDENCE_BASE_URL}/internal/tasks/{task_id}/context",
                json=context,
                timeout=10.0,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        _log.warning(
            "evidence put_context task_id=%s %s",
            task_id,
            evidence_http_error_detail(e.response),
        )
    except httpx.HTTPError as e:
        _log.warning("evidence put_context task_id=%s transport_error=%s", task_id, e)


async def get_context(task_id: str) -> Dict[str, Any]:
    """读取 Evidence 中持久化的任务上下文。

    查询失败时返回空对象，调用方可继续使用内存态；这与 ``put_context``
    的最佳努力语义保持一致。
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{EVIDENCE_BASE_URL}/internal/tasks/{task_id}/context",
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}
    except httpx.HTTPStatusError as e:
        _log.warning(
            "evidence get_context task_id=%s %s",
            task_id,
            evidence_http_error_detail(e.response),
        )
    except httpx.HTTPError as e:
        _log.warning("evidence get_context task_id=%s transport_error=%s", task_id, e)
    return {}


async def list_internal_tasks(limit: int = 200) -> list[dict[str, Any]]:
    """列出 Evidence 中的任务摘要，供服务启动恢复遗留任务。"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{EVIDENCE_BASE_URL}/internal/tasks",
                params={"limit": max(1, min(int(limit), 200))},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
    except httpx.HTTPStatusError as e:
        _log.warning("evidence list_internal_tasks %s", evidence_http_error_detail(e.response))
    except httpx.HTTPError as e:
        _log.warning("evidence list_internal_tasks transport_error=%s", e)
    return []


async def put_artifacts_summary(task_id: str, skill_id: str, summary: str) -> None:
    """将本步执行结果摘要写入Evidence artifacts（证据落盘）。"""
    if not summary or len(summary) > 10000:
        summary = (summary or "")[:10000]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{EVIDENCE_BASE_URL}/internal/tasks/{task_id}/artifacts",
                json={
                    "skill_id": skill_id,
                    "summary": summary,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        _log.warning(
            "evidence put_artifacts task_id=%s skill_id=%s %s",
            task_id,
            skill_id,
            evidence_http_error_detail(e.response),
        )
    except httpx.HTTPError as e:
        _log.warning(
            "evidence put_artifacts task_id=%s skill_id=%s transport_error=%s",
            task_id,
            skill_id,
            e,
        )
