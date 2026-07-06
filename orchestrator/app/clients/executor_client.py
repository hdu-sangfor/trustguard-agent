import os
import asyncio
import random
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException

from app.models import ExecuteSkillResponse
from app.core.agent_context import build_execute_context
from app.core.execution_kind import resolve_execution_kind

EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://localhost:18102")


def build_executor_execute_json_payload(
    task_id: str,
    skill_id: str,
    target: str,
    params: Dict[str, Any],
    allowed_target: str | None = None,
    context: Dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    execution_kind: str | None = None,
) -> Dict[str, Any]:
    """
    构造发往 Executor `POST /v1/execute` 的 JSON 体（与 MQ 侧 `MQExecuteTaskMessage` 语义对齐）。

    r4f-e：HTTP 与 MQ 双模式应对同一套 `params` / 过滤后 `context`；单测与本函数为契约门禁。
    """
    filtered_ctx = build_execute_context(context or {})
    ek = resolve_execution_kind(skill_id=skill_id, execution_kind=execution_kind)
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "skill_id": skill_id,
        "target": target,
        "params": params or {},
        "allowed_target": allowed_target,
        "context": filtered_ctx,
        "execution_kind": ek,
    }
    if request_id:
        payload["request_id"] = request_id
    return payload

_LONG_HTTP_SKILLS = frozenset(
    {
        "katana",
        "dirsearch",
        "dispatcher",
        "nuclei",
        "nikto-scan",
        "fenjing",
        "web-vuln-pipeline",
    }
)


def _http_read_timeout_seconds(skill_id: str, *, params: Dict[str, Any] | None = None) -> float:
    """HTTP 同步 /v1/execute 的读超时。

    优先使用 params["timeout"]（Plan 项显式超时）+ overhead，确保 HTTP 读超时不早于
    执行器内部工具超时（避免 dirsearch/katana 等长任务被过早截断并触发多轮重试）。
    无 params 时按 skill_id 分级：长技能默认 600s，其余 120s。
    """
    sid = (skill_id or "").strip().lower()
    if params:
        try:
            pt = float(params.get("timeout") or 0)
            if pt > 60:
                overhead = float(os.getenv("EXECUTOR_HTTP_TIMEOUT_OVERHEAD_SECONDS", "60"))
                floor = float(os.getenv("EXECUTOR_HTTP_LONG_READ_TIMEOUT_SECONDS", "600"))
                return max(pt + overhead, floor)
        except (TypeError, ValueError):
            pass
    if sid in _LONG_HTTP_SKILLS:
        return float(os.getenv("EXECUTOR_HTTP_LONG_READ_TIMEOUT_SECONDS", "600"))
    return float(os.getenv("EXECUTOR_HTTP_READ_TIMEOUT_SECONDS", "120"))


async def fetch_skills_for_phase(phase: str) -> List[str]:
    """从执行器 GET /v1/skills?phase= 拉取当前阶段可用 skill_id 列表；失败时返回空列表。"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{EXECUTOR_BASE_URL}/v1/skills",
                params={"phase": phase},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return list(data.get("skill_ids") or [])
    except Exception:
        return []


async def call_executor(
    task_id: str,
    skill_id: str,
    target: str,
    params: Dict[str, Any],
    allowed_target: str | None = None,
    context: Dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    execution_kind: str | None = None,
) -> ExecuteSkillResponse:
    """调用 Executor /v1/execute 执行具体工具；allowed_target 用于执行器范围校验。"""
    payload = build_executor_execute_json_payload(
        task_id,
        skill_id,
        target,
        params,
        allowed_target=allowed_target,
        context=context,
        request_id=request_id,
        execution_kind=execution_kind,
    )
    last_exc = None
    max_attempts = int(os.getenv("EXECUTOR_MAX_RETRIES", "4"))
    read_sec = _http_read_timeout_seconds(skill_id, params=params)
    timeout = httpx.Timeout(read_sec, connect=10.0, read=read_sec, write=20.0, pool=20.0)
    # 长超时 skill（params.timeout > 120s）不做多轮重试：超时说明执行器仍在跑，
    # 重试只会叠加容器、延长总等待时间，并可能让执行器同时运行多个重复任务。
    try:
        _explicit_timeout = float((params or {}).get("timeout") or 0)
    except (TypeError, ValueError):
        _explicit_timeout = 0.0
    if _explicit_timeout > 120:
        max_attempts = min(max_attempts, int(os.getenv("EXECUTOR_MAX_RETRIES_LONG_SKILL", "1")))
    retriable_statuses = {429, 502, 503, 504}
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{EXECUTOR_BASE_URL}/v1/execute", json=payload, timeout=timeout)
                resp.raise_for_status()
                return ExecuteSkillResponse.model_validate(resp.json())
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt >= max_attempts - 1:
                raise HTTPException(status_code=502, detail="skill executor unavailable (timeout or connection)") from exc
            backoff = min(5.0, (0.4 * (2**attempt)) + random.uniform(0.0, 0.25))
            await asyncio.sleep(backoff)
            continue
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in retriable_statuses and attempt < max_attempts - 1:
                backoff = min(5.0, (0.4 * (2**attempt)) + random.uniform(0.0, 0.25))
                await asyncio.sleep(backoff)
                continue
            detail = "skill executor unavailable"
            try:
                data = exc.response.json()
                detail = str(data.get("detail") or data)
            except Exception:
                txt = (exc.response.text or "").strip()
                if txt:
                    detail = txt
            raise HTTPException(
                status_code=502,
                detail=f"skill executor returned {status}: {detail}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="skill executor unavailable") from exc
    raise HTTPException(status_code=502, detail="skill executor unavailable") from last_exc


async def fetch_executor_artifact(
    *,
    task_id: str,
    artifact_ref: str,
    include_raw: bool = False,
) -> dict[str, Any] | None:
    """从 Executor /v1/artifacts 拉取 artifact（用于挂载差异或短暂读盘竞态时兜底）。"""
    ref = (artifact_ref or "").strip()
    if not task_id or not ref:
        return None
    max_attempts = int(os.getenv("EXECUTOR_ARTIFACT_READ_MAX_RETRIES", "4"))
    timeout = httpx.Timeout(15.0, connect=5.0, read=15.0, write=10.0, pool=10.0)
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{EXECUTOR_BASE_URL}/v1/artifacts/{ref}",
                    params={"task_id": task_id, "include_raw": include_raw},
                    timeout=timeout,
                )
                if resp.status_code == 404:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(min(2.0, 0.2 * (2**attempt)))
                        continue
                    return None
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else None
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            if attempt < max_attempts - 1:
                await asyncio.sleep(min(2.0, 0.2 * (2**attempt)))
                continue
            return None
        except Exception:
            return None
    return None
