"""
Executor Service — 接收编排器下发的 Skill 执行请求，调用具体 Skill 并返回结构化结果。

契约：POST /v1/execute，见架构文档 12.3。
红队安全：若请求带 allowed_target，仅允许对与该目标同主机的 target 执行，禁止内网扩大化。
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse
import shlex

from fastapi import FastAPI, HTTPException, Query

# 背压：全局并发执行上限，0 表示不限制
_MAX_CONCURRENT_EXECUTIONS = int(os.getenv("MAX_CONCURRENT_EXECUTIONS", "0"))
_execution_semaphore: asyncio.Semaphore | None = (
    asyncio.Semaphore(_MAX_CONCURRENT_EXECUTIONS) if _MAX_CONCURRENT_EXECUTIONS > 0 else None
)

from app.models import SkillRequest, SkillResult
from app.skills.registry import get_skill, list_skill_ids, refresh_skills
from app.worker_daemon.sniff_pool import ArtifactSniffPool
from app.tools_registry import get_skill_ids_for_phase, list_skills_with_category
from pathlib import Path

from app.core.workspace_store import (
    count_http_url_lines,
    find_latest_katana_urls_file,
    prepare_artifact_slot,
    _to_workspace_relative,
    read_artifact,
    write_artifact,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _executor_lifespan(_app: FastAPI):
    yield


app = FastAPI(title="Executor Service", version="0.2.0", lifespan=_executor_lifespan)

# `v1_execution_plane.schema_version` 单一真源； bump 时请同步 `_V1_EXECUTION_PLANE_SCHEMA7_KEYS` 等契约（见单测）。
V1_EXECUTION_PLANE_SCHEMA_VERSION: int = 7


def _require_execution_kind() -> bool:
    """EXECUTOR_REQUIRE_EXECUTION_KIND=1 时拒绝未显式携带 execution_kind 的请求（阶段 E 收紧）。"""
    raw = (os.getenv("EXECUTOR_REQUIRE_EXECUTION_KIND") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _v1_execution_plane_summary() -> dict[str, object]:
    """只读摘要：与编排器 `v1_mq_lanes` 同源；MQ 模式仅 **`MQ_TOPIC_AGENT`** 单队列消费。"""
    from app.micro_executor.outputs import _default_max_agent_chars

    containers = os.getenv("EXECUTOR_USE_SKILL_CONTAINERS", "true").lower() == "true"
    mq_topic_agent = (os.getenv("MQ_TOPIC_AGENT") or "execute_tasks_agent").strip() or "execute_tasks_agent"
    mq_broker_configured = bool((os.getenv("MQ_BROKER_URL") or "").strip())
    mode = (os.getenv("EXECUTION_DISPATCH_MODE") or "http").strip().lower()
    if mode not in ("http", "mq"):
        mode = "http"
    mq_dispatch_ready = mode == "mq" and mq_broker_configured
    return {
        "schema_version": V1_EXECUTION_PLANE_SCHEMA_VERSION,
        "execution_dispatch_mode": mode,
        "mq_fast_lane_module": "app.mq_execute_consumer",
        "mq_agent_lane_module": "app.mq_agent_daemon",
        "mq_topic_execute": mq_topic_agent,
        "mq_topic_agent": mq_topic_agent,
        "mq_broker_configured": mq_broker_configured,
        "mq_dispatch_ready": mq_dispatch_ready,
        "agent_lane_mq_enabled": mq_dispatch_ready,
        "agent_lane_allowlist_count": 0,
        "agent_lane_routing_active": mq_dispatch_ready,
        "agent_lane_publish_ready": mq_dispatch_ready,
        "dev_cli_hints": ["dev/mq/mq_worker.py", "dev/mq/mq_agent_daemon.py"],
        "agent_stdout_summary_default_max_chars": _default_max_agent_chars(),
        "skill_containers_enabled": containers,
        "require_execution_kind": _require_execution_kind(),
    }

_LOCALHOST_ALIAS = (os.getenv("EXECUTOR_LOCALHOST_ALIAS") or "host.docker.internal").strip()
_POINTER_ONLY = os.getenv("EXECUTOR_POINTER_ONLY", "true").lower() == "true"
# POINTER_ONLY 模式下仍须把结构化结果回传给编排器/LLM，否则指纹、读工件等工具表现为「成功但无内容」。
_POINTER_PRESERVE_FULL_PARSED_SKILLS = frozenset(
    {
        "read_workspace_artifact",
        "read_target_list",
        "httpx",
        "ehole",
        "http-enum",
        "curl-raw",
        "whatweb-fingerprint",
        "baidu-search",
    }
)


def _host_of(s: str) -> str:
    """从 URL 或 host:port 中提取 host，用于范围校验。"""
    s = (s or "").strip()
    if not s:
        return ""
    if "://" in s:
        try:
            return urlparse(s).hostname or s.split("/")[0].split(":")[0] or ""
        except Exception:
            return s.split("/")[0].split(":")[0] or ""
    return s.split(":")[0].split("/")[0] or ""


def _normalize_scope_host(host: str) -> str:
    h = (host or "").strip().lower()
    if not h:
        return ""
    if h in ("127.0.0.1", "localhost", "::1"):
        return _LOCALHOST_ALIAS.lower() if _LOCALHOST_ALIAS else "host.docker.internal"
    return h


def _is_target_allowed(target: str, allowed_target: str | None, skill_id: str) -> bool:
    """仅当 allowed_target 未设置或 target 与 allowed_target 同主机时允许；搜索类 skill 无主机目标则放行。"""
    if not allowed_target or not allowed_target.strip():
        return True
    # 搜索类 skill（如 baidu-search）的 target 为查询串而非主机，不做主机范围校验
    if (skill_id or "").lower() in ("baidu-search", "web_search", "search"):
        return True
    return _normalize_scope_host(_host_of(target)) == _normalize_scope_host(_host_of(allowed_target))


def _rewrite_localhost_target_for_container(target: str) -> str:
    """
    容器内访问 127.0.0.1/localhost 只会指向容器自身。
    若配置了 EXECUTOR_LOCALHOST_ALIAS，则将本地回环目标改写为该别名（默认 host.docker.internal）。
    """
    t = (target or "").strip()
    if not t or not _LOCALHOST_ALIAS:
        return t

    if "://" in t:
        parsed = urlparse(t)
        host = parsed.hostname or ""
        if host not in ("127.0.0.1", "localhost"):
            return t
        netloc = _LOCALHOST_ALIAS
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()

    if t.startswith("127.0.0.1:"):
        return t.replace("127.0.0.1", _LOCALHOST_ALIAS, 1)
    if t.startswith("localhost:"):
        return t.replace("localhost", _LOCALHOST_ALIAS, 1)
    if t in ("127.0.0.1", "localhost"):
        return _LOCALHOST_ALIAS
    return t


def _apply_nmap_scope_guard(req: SkillRequest) -> SkillRequest:
    if (req.skill_id or "").lower() != "nmap":
        return req
    ctx = req.context or {}
    if str(ctx.get("target_scope_mode") or "") != "single_port":
        return req
    allowed_ports = ctx.get("allowed_ports")
    if not isinstance(allowed_ports, list) or not allowed_ports:
        return req
    port = allowed_ports[0]
    try:
        port = int(port)
    except Exception:
        return req
    params = dict(req.params or {})
    params["ports"] = str(port)
    params.pop("top_ports", None)
    for key in ("args", "arguments"):
        raw = params.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        kept: list[str] = []
        tokens = shlex.split(raw)
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in ("-p", "--top-ports", "-p-"):
                if t in ("-p", "--top-ports") and i + 1 < len(tokens):
                    i += 2
                else:
                    i += 1
                continue
            if t.startswith("-p") and t != "-Pn":
                i += 1
                continue
            kept.append(t)
            i += 1
        params[key] = " ".join(kept)
    return req.model_copy(update={"params": params})


def _recover_katana_if_workspace_has_output(req: SkillRequest, result: SkillResult) -> SkillResult:
    """
    Katana 在共享卷上先写完 discovery/katana_urls.txt，但外层 docker run 仍可能先触发超时。
    若磁盘上已有有效 URL 列表，则将本次标为 SUCCESS，避免编排器与事实不一致。
    """
    if (req.skill_id or "").strip().lower() != "katana":
        return result
    st = (result.status or "").upper()
    if st in ("OK", "SUCCESS"):
        return result
    ku = find_latest_katana_urls_file(req.task_id)
    if ku is None:
        return result
    n = count_http_url_lines(ku)
    if n <= 0:
        return result
    run_id = ku.parent.parent.name
    run_root = str(ku.parent.parent.resolve())
    merged = dict(result.parsed_artifacts or {})
    prev_diag = merged.get("diagnostics") if isinstance(merged.get("diagnostics"), dict) else {}
    ds = 0
    if isinstance(merged.get("url_counts"), dict):
        try:
            ds = int((merged.get("url_counts") or {}).get("dirsearch") or 0)
        except Exception:
            ds = 0
    merged.update(
        {
            "workspace_recovery": True,
            "recovery_reason": "executor_outer_timeout_or_error_but_katana_urls_present",
            "run_id": run_id,
            "run_root": run_root,
            "target_url": req.target,
            "url_counts": {"katana": n, "dirsearch": ds, "total_raw": n + ds},
            "partial_results": True,
            "phases_completed": merged.get("phases_completed") or ["katana"],
            "relative_paths": {
                "katana_urls": "discovery/katana_urls.txt",
                "dirsearch_json": "discovery/dirsearch.json",
            },
            "diagnostics": {**prev_diag, "recovered_from_workspace": True, "prior_status": result.status},
        }
    )
    return SkillResult(
        status="SUCCESS",
        parsed_artifacts=merged,
        raw_stdout=result.raw_stdout or "",
        raw_stderr=(result.raw_stderr or "") + f" [recovered from workspace: {ku}]",
        duration_ms=result.duration_ms,
        artifact_refs_v1=result.artifact_refs_v1,
        usage=result.usage,
        incremental_artifacts=result.incremental_artifacts,
    )


@app.post("/v1/execute", response_model=SkillResult)
async def execute(req: SkillRequest) -> SkillResult:
    if _execution_semaphore is not None:
        await _execution_semaphore.acquire()
    try:
        return await _execute_impl(req)
    finally:
        if _execution_semaphore is not None:
            _execution_semaphore.release()


async def _execute_impl(
    req: SkillRequest,
    *,
    artifact_sniff_pool: ArtifactSniffPool | None = None,
) -> SkillResult:
    request_id = req.request_id or ""
    logger.info(
        "execute start task_id=%s skill_id=%s request_id=%s execution_kind=%s explicit=%s",
        req.task_id,
        req.skill_id,
        request_id or "(none)",
        req.execution_kind,
        req.execution_kind_explicit,
    )
    # 阶段 E 收紧：全量升级完成后启用 EXECUTOR_REQUIRE_EXECUTION_KIND=1 拒绝无 execution_kind 请求
    if _require_execution_kind() and not req.execution_kind_explicit:
        raise HTTPException(
            status_code=400,
            detail=(
                "execution_kind is required (EXECUTOR_REQUIRE_EXECUTION_KIND=1); "
                "caller must explicitly set execution_kind='skill'"
            ),
        )
    if not _is_target_allowed(req.target, req.allowed_target, req.skill_id):
        raise HTTPException(
            status_code=403,
            detail="target not allowed: executor only accepts the task-authorized target (no scope creep)",
        )
    # 仅当使用技能容器时重写 127.0.0.1 → host.docker.internal，使容器内能访问宿主机端口；
    # Worker 在宿主机上以子进程跑 skill 时不应重写，否则 curl 可能连不上或行为异常。
    use_skill_containers = os.getenv("EXECUTOR_USE_SKILL_CONTAINERS", "true").lower() == "true"
    resolved_target = _rewrite_localhost_target_for_container(req.target) if use_skill_containers else req.target
    req_for_exec = req.model_copy(update={"target": resolved_target})
    req_for_exec = _apply_nmap_scope_guard(req_for_exec)
    phase = str((req_for_exec.context or {}).get("phase") or "UNKNOWN")
    artifact_ref_pre: str | None = None
    artifact_base: Path | None = None
    try:
        artifact_ref_pre, artifact_base, _ = prepare_artifact_slot(
            task_id=req.task_id,
            phase=phase,
            skill_id=req.skill_id,
        )
        ctx = dict(req_for_exec.context or {})
        ctx["executor_artifact_ref"] = artifact_ref_pre
        ctx["executor_artifact_base"] = _to_workspace_relative(artifact_base)
        req_for_exec = req_for_exec.model_copy(update={"context": ctx})
    except Exception as exc:
        logger.warning("prepare_artifact_slot failed task_id=%s: %s", req.task_id, exc)
        artifact_ref_pre = None
        artifact_base = None

    if req.execution_kind == "skill":
        skill = get_skill(req.skill_id)
        if skill is None:
            raise HTTPException(status_code=400, detail=f"unknown skill_id: {req.skill_id}")
        result = await asyncio.to_thread(skill.execute, req_for_exec)
    else:  # pragma: no cover — SkillRequest 校验后不应落到此分支
        raise HTTPException(status_code=400, detail="invalid execution_kind (expected skill)")
    result = _recover_katana_if_workspace_has_output(req_for_exec, result)
    if artifact_sniff_pool is not None:
        for line in (result.raw_stderr or "").splitlines():
            artifact_sniff_pool.ingest_line(line)
    try:
        if artifact_base is not None and artifact_ref_pre:
            artifact_ref = write_artifact(
                task_id=req.task_id,
                phase=phase,
                skill_id=req.skill_id,
                request_payload=req_for_exec.model_dump(),
                status=result.status,
                duration_ms=result.duration_ms,
                raw_stdout=result.raw_stdout,
                raw_stderr=result.raw_stderr,
                parsed_artifacts=result.parsed_artifacts or {},
                artifact_base=artifact_base,
                artifact_ref_precomputed=artifact_ref_pre,
            )
        else:
            artifact_ref = write_artifact(
                task_id=req.task_id,
                phase=phase,
                skill_id=req.skill_id,
                request_payload=req_for_exec.model_dump(),
                status=result.status,
                duration_ms=result.duration_ms,
                raw_stdout=result.raw_stdout,
                raw_stderr=result.raw_stderr,
                parsed_artifacts=result.parsed_artifacts or {},
            )
        if _POINTER_ONLY:
            sid = (req.skill_id or "").strip().lower()
            if sid in _POINTER_PRESERVE_FULL_PARSED_SKILLS:
                merged_p = dict(result.parsed_artifacts or {})
                merged_p["artifact_ref"] = artifact_ref
                result = SkillResult(
                    status=result.status,
                    parsed_artifacts=merged_p,
                    raw_stdout="",
                    raw_stderr="",
                    duration_ms=result.duration_ms,
                    usage=result.usage,
                    incremental_artifacts=result.incremental_artifacts,
                )
            else:
                result = SkillResult(
                    status=result.status,
                    parsed_artifacts={
                        "artifact_ref": artifact_ref,
                        "summary_keys": list((result.parsed_artifacts or {}).keys())[:20],
                    },
                    raw_stdout="",
                    raw_stderr="",
                    duration_ms=result.duration_ms,
                    usage=result.usage,
                    incremental_artifacts=result.incremental_artifacts,
                )
        else:
            merged = dict(result.parsed_artifacts or {})
            merged["artifact_ref"] = artifact_ref
            result.parsed_artifacts = merged
    except Exception:
        pass
    # 保持执行状态的真实性：有 artifact_ref 仅代表落盘成功，不代表技能执行成功。
    # 否则会导致编排器把失败结果当成成功并污染后续记忆/KB。
    logger.info(
        "execute done task_id=%s skill_id=%s request_id=%s status=%s duration_ms=%s",
        req.task_id,
        req.skill_id,
        request_id or "(none)",
        result.status,
        result.duration_ms,
    )
    if (result.status or "").upper() not in ("OK", "SUCCESS"):
        err_msg = (result.parsed_artifacts or {}).get("error") or result.raw_stderr or ""
        if err_msg:
            logger.warning("execute failed detail: skill_id=%s target=%s stderr/error=%s", req.skill_id, req_for_exec.target, err_msg[:500])
    if artifact_sniff_pool is not None:
        refs = artifact_sniff_pool.artifact_refs_ordered()
        if refs:
            result = result.model_copy(update={"artifact_refs_v1": list(refs)})
    return result


@app.get("/v1/skills")
async def list_skills(phase: str | None = Query(default=None, description="编排器阶段，如 RECON、VULN_SCAN、EXPLOIT")) -> dict:
    """
    返回当前 Executor 可用的 skill 列表；数据源为 docker/tools_registry.yaml（Skill 目录）。
    若传 phase，只返回该阶段允许的 skill_id（按 category 过滤）；与 registry 取交集保证可执行。
    """
    impl_ids = set(refresh_skills())
    if phase:
        phase_skill_ids = set(get_skill_ids_for_phase(phase))
        skill_ids = sorted(impl_ids & phase_skill_ids)
    else:
        registry_skills = [s["skill_id"] for s in list_skills_with_category() if s["skill_id"] in impl_ids]
        skill_ids = sorted(set(registry_skills)) if registry_skills else sorted(impl_ids)
    skills_meta = [s for s in list_skills_with_category() if s["skill_id"] in skill_ids]
    return {"skill_ids": skill_ids, "skills": skills_meta}


@app.get("/v1/artifacts/{artifact_ref:path}")
async def get_artifact(
    artifact_ref: str,
    task_id: str = Query(..., description="Task ID binding for artifact access control"),
    include_raw: bool = Query(default=False),
) -> dict:
    payload = read_artifact(artifact_ref=artifact_ref, include_raw=include_raw)
    if payload is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    meta_task_id = str((payload.get("meta") or {}).get("task_id") or "")
    if meta_task_id != str(task_id):
        raise HTTPException(status_code=403, detail="artifact access denied: task_id mismatch")
    return payload


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "v1_execution_plane": _v1_execution_plane_summary()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=18102, reload=True)
