"""
Orchestrator Service — 编排器：主导渗透测试控制方向，支持单 Agent 与 Manager Agent + Todo 两种模式（v1）。

- 创建/查询任务、单步 tick、以及「运行至完成」的编排循环均由本服务提供。
- 执行由 Executor 负责；本服务通过 state_machine.tick 完成「决策 → 执行 → 留痕」闭环。
"""
import json
import logging
import os
import asyncio
import random
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote, urlparse

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.models import (
    CreateTaskPayload,
    OrchestratorTaskStateResponse,
    Phase,
    TaskState,
    TaskStatus,
    TraceEvent,
    phase_wall_clock_limit_sec_from_env,
)
from app.clients.trace_client import emit_trace
from app.clients.llm_client import LLMCallFailed
from app.clients.checkpoint_client import (
    save_checkpoint as save_checkpoint_remote,
    load_checkpoint as load_checkpoint_remote,
)
from app.trustguard_agent.graph import run_agent as run_langgraph_agent
from app.trustguard_agent.models import AgentRunRequest, AgentRunResponse
from app.core.state_machine import (
    tick as state_machine_tick,
    tick_manager as state_machine_tick_manager,
)
from app.core.task_store import TaskRecord, TaskStore, get_task_store_from_env
from app.core.phase_clock_restore import hydrate_phase_clock_from_checkpoint_and_store


def _checkpoint_timing_kwargs(state: TaskState) -> dict[str, Any]:
    ps: str | None = None
    if getattr(state, "phase_start_at", None):
        ps = state.phase_start_at.isoformat() + "Z"
    lim: int | None = getattr(state, "current_phase_duration_limit_sec", None)
    return {
        "phase_start_at": ps,
        "current_phase_duration_limit_sec": lim,
        # FinOps 断点持久化：随 checkpoint 写入，供 Redis 清空后续跑时还原
        "llm_input_tokens_total": int(getattr(state, "llm_input_tokens_total", 0) or 0),
        "llm_output_tokens_total": int(getattr(state, "llm_output_tokens_total", 0) or 0),
        "cumulative_cost_usd": float(getattr(state, "cumulative_cost_usd", 0.0) or 0.0),
    }


def _reset_phase_clock_for_resume(state: TaskState) -> None:
    """续跑应从本次恢复时间重新计阶段墙钟，避免暂停等待时间触发阶段预算。"""
    state.hydrate_phase_clock(
        current_phase=state.current_phase,
        phase_start_at=datetime.utcnow(),
        current_phase_duration_limit_sec=phase_wall_clock_limit_sec_from_env(),
    )


from app.plan_feature_flags import orch_plan_mode_enabled
from app.kb_manual_admin import (
    ManualKnowledgeApproveBody,
    ManualKnowledgeIngestBody,
    require_kb_manual_ingest_token,
    run_manual_knowledge_approve,
    run_manual_knowledge_ingest,
)
from app.kb_pipeline_admin import require_kb_pipeline_admin_token
from app.core.chunk_store import (
    CHUNK_BATCH_GET_MAX,
    ChunkStoreError,
    adjust_chunk_ref,
    chunk_store_error_http_detail,
    gc_enabled,
    gc_sweep_all_tasks,
    gc_sweep_task,
    max_ref_adjust_batch,
    read_chunk,
    read_chunks_batch,
    set_chunk_retention,
    write_chunk,
)
from app.core.chunk_store_metrics import snapshot as chunk_store_metrics_snapshot
from app.core.skill_pack_loader import (
    SkillPackLoadError,
    load_skill_pack,
    skill_pack_error_http_detail,
)
from app.skill_pack_models import SkillPack
from app.orchestrator_sli_metrics import record_tick_attempt
from app.orchestrator_trace import (
    clamp_execution_paging_limit,
    clamp_execution_paging_offset,
    compile_section_from_context,
    execution_record_to_dict,
    finalize_execution_for_trace_response,
    parse_execution_fields_param,
    plan_section_from_context,
    prepare_compile_section_api,
    prepare_plan_section_api,
    prepare_tactical_incremental_section_api,
    require_orch_trace_token_if_set,
)
from app.core.v1_agent_registry import build_registry_from_env
from app.core.v1_scheduler_policy import (
    SchedulingRequest,
    build_v1_scheduling_policy_from_env,
    scheduling_role_filter_enabled,
)
from app.core.execution_dispatcher import v1_mq_lane_health_summary
from app.core.v1_kb_federation_store import (
    KbFederationStoreError,
    get_kb_federation_meta_store,
    kb_federation_store_backend_from_env,
    kb_federation_store_enabled_from_env,
    normalize_federation_meta,
)
from app.core.v1_kb_federation_sync import (
    kb_federation_sync_enabled_from_env,
    kb_federation_sync_interval_seconds,
    run_kb_federation_reconcile_once,
)
from app.clients.kb_client import get_kb_config

logger = logging.getLogger(__name__)

ENABLE_EXECUTOR = os.getenv("ENABLE_EXECUTOR", "true").lower() == "true"
# 是否启用 Manager Agent + Todo（默认 false 为单 Agent 模式）
ENABLE_MANAGER_AGENT = os.getenv("ENABLE_MANAGER_AGENT", "false").lower() == "true"
# 执行派发模式：http 同步调用执行器，mq 发布到队列由 Worker 消费
EXECUTION_DISPATCH_MODE = (os.getenv("EXECUTION_DISPATCH_MODE", "http") or "http").strip().lower()
if EXECUTION_DISPATCH_MODE not in ("http", "mq"):
    EXECUTION_DISPATCH_MODE = "http"
# 背压：单任务最大在途执行数（MQ 模式下投递前检查，默认 1 与当前逐 tick 行为一致）
# 0 = 不限制；>=1 时 ExecutionDispatcher 对每 task 并发派发 skill 做背压（HTTP/MQ 均生效）
MAX_IN_FLIGHT_SKILLS_PER_TASK = max(0, int(os.getenv("MAX_IN_FLIGHT_SKILLS_PER_TASK", "1") or "1"))
# 并发策略：全局最多允许同时 run 的任务数，0 表示不限制
MAX_CONCURRENT_TASKS_RUNNING = int(os.getenv("MAX_CONCURRENT_TASKS_RUNNING", "0"))
CHUNK_GC_INTERVAL_SECONDS = max(0, int(os.getenv("CHUNK_GC_INTERVAL_SECONDS", "0") or "0"))


@asynccontextmanager
async def _orchestrator_lifespan(app: FastAPI):
    gc_task: asyncio.Task | None = None
    promo_task: asyncio.Task | None = None
    cve_pipe_task: asyncio.Task | None = None
    blog_pipe_task: asyncio.Task | None = None
    fed_sync_task: asyncio.Task | None = None
    if CHUNK_GC_INTERVAL_SECONDS > 0 and gc_enabled():

        async def _chunk_gc_loop() -> None:
            while True:
                await asyncio.sleep(CHUNK_GC_INTERVAL_SECONDS)
                try:
                    await asyncio.to_thread(gc_sweep_all_tasks)
                except Exception:
                    logger.exception("chunk gc_sweep_all_tasks failed")

        gc_task = asyncio.create_task(_chunk_gc_loop())

    from app.kb_experience_promotion import (
        kb_experience_promotion_interval_seconds,
        sweep_kb_experience_promotions,
    )

    _promo_iv = kb_experience_promotion_interval_seconds()
    if _promo_iv > 0:

        async def _kb_experience_promotion_loop() -> None:
            while True:
                await asyncio.sleep(_promo_iv)
                try:
                    await sweep_kb_experience_promotions()
                except Exception:
                    logger.exception("kb_experience_promotion sweep failed")

        promo_task = asyncio.create_task(_kb_experience_promotion_loop())

    from app.kb_pipeline_runners import (
        kb_blog_fetch_interval_seconds,
        kb_blog_source_urls,
        kb_cve_sync_interval_seconds,
        kb_pipeline_blog_enabled,
        kb_pipeline_cve_enabled,
        run_blog_fetch_once,
        run_cve_nvd_sync_once,
    )

    _cve_pipe_iv = kb_cve_sync_interval_seconds()
    if _cve_pipe_iv > 0 and kb_pipeline_cve_enabled():

        async def _kb_cve_pipeline_loop() -> None:
            while True:
                await asyncio.sleep(_cve_pipe_iv)
                try:
                    await run_cve_nvd_sync_once()
                except Exception:
                    logger.exception("kb_pipeline cve sync loop failed")

        cve_pipe_task = asyncio.create_task(_kb_cve_pipeline_loop())

    _blog_pipe_iv = kb_blog_fetch_interval_seconds()
    if _blog_pipe_iv > 0 and kb_pipeline_blog_enabled() and kb_blog_source_urls():

        async def _kb_blog_pipeline_loop() -> None:
            while True:
                await asyncio.sleep(_blog_pipe_iv)
                try:
                    await run_blog_fetch_once()
                except Exception:
                    logger.exception("kb_pipeline blog fetch loop failed")

        blog_pipe_task = asyncio.create_task(_kb_blog_pipeline_loop())

    _fed_sync_iv = kb_federation_sync_interval_seconds()
    if (
        _fed_sync_iv > 0
        and kb_federation_store_enabled_from_env()
        and kb_federation_sync_enabled_from_env()
    ):

        async def _kb_federation_sync_loop() -> None:
            while True:
                await asyncio.sleep(_fed_sync_iv)
                try:
                    stats = await asyncio.to_thread(run_kb_federation_reconcile_once)
                    if stats:
                        logger.info(
                            "kb_federation_sync reconcile ok stale_task_refs=%s orphan_all=%s repairs=%s",
                            stats.get("stale_task_refs_removed", 0),
                            stats.get("orphan_all_ids_removed", 0),
                            stats.get("task_index_repairs", 0),
                        )
                except Exception:
                    logger.exception("kb_federation_sync reconcile failed")

        fed_sync_task = asyncio.create_task(_kb_federation_sync_loop())

    yield

    if gc_task is not None:
        gc_task.cancel()
        with suppress(asyncio.CancelledError):
            await gc_task
    if promo_task is not None:
        promo_task.cancel()
        with suppress(asyncio.CancelledError):
            await promo_task
    if cve_pipe_task is not None:
        cve_pipe_task.cancel()
        with suppress(asyncio.CancelledError):
            await cve_pipe_task
    if blog_pipe_task is not None:
        blog_pipe_task.cancel()
        with suppress(asyncio.CancelledError):
            await blog_pipe_task
    if fed_sync_task is not None:
        fed_sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await fed_sync_task

app = FastAPI(
    title="Orchestrator Service",
    version="0.4.0",
    description="",
    lifespan=_orchestrator_lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """将未捕获异常转为 500 并返回具体信息，便于Gateway转发的 orchestrator body 能看到原因。"""
    logger.exception("orchestrator unhandled exception: %s", exc)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__},
    )


@app.post("/v1/agent/run", response_model=AgentRunResponse)
def run_agent(req: AgentRunRequest) -> AgentRunResponse:
    return run_langgraph_agent(req)


_TASKS: dict[str, TaskState] = {}
_RUN_JOBS: dict[str, dict[str, Any]] = {}
# v1：TaskStore 作为任务元信息的单一事实来源；根据环境选择内存或 Redis 实现。
_TASK_STORE: TaskStore = get_task_store_from_env()
_V1_AGENT_REGISTRY = build_registry_from_env()
_V1_SCHEDULING_POLICY = build_v1_scheduling_policy_from_env()


def _kb_federation_observe_enabled() -> bool:
    raw = (os.getenv("V1_KB_FEDERATION_OBSERVE_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _planitem_dispatch_enabled() -> bool:
    raw = (os.getenv("V1_PLANITEM_DISPATCH_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _attach_planitem_observe_fields(body: dict[str, Any]) -> None:
    """
    将 PlanItem 旁路元数据合并进 scheduling-observe 响应（仅只读；Flag 关闭时不读取 METADATA JSON）。
    见 v1-planitem-boundary.md §4.1。
    """
    enabled = _planitem_dispatch_enabled()
    body["plan_item_dispatch_enabled"] = enabled
    if not enabled:
        return
    raw = (os.getenv("V1_PLANITEM_METADATA_JSON") or "").strip()
    if not raw:
        return
    try:
        obj = json.loads(raw)
    except Exception:
        body["plan_item_dispatch_error"] = "parse_failed"
        return
    if not isinstance(obj, dict):
        body["plan_item_dispatch_error"] = "parse_failed"
        return

    def _scalar_to_str(v: Any) -> str | None:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return None

    pid_raw = obj.get("plan_item_id")
    pid = _scalar_to_str(pid_raw) if pid_raw is not None else None
    if not pid:
        body["plan_item_dispatch_error"] = "invalid_metadata"
        return

    item: dict[str, Any] = {"plan_item_id": pid}
    if "plan_item_phase" in obj and obj["plan_item_phase"] is not None:
        ph = _scalar_to_str(obj["plan_item_phase"])
        if ph is not None:
            item["plan_item_phase"] = ph
    if "plan_item_parent_ref" in obj and obj["plan_item_parent_ref"] is not None:
        pr = _scalar_to_str(obj["plan_item_parent_ref"])
        if pr is not None:
            item["plan_item_parent_ref"] = pr
    body["plan_item"] = item


def _v1_kb_health_summary() -> dict[str, Any]:
    """与 `/health`、`overview` 共用的 KB 只读摘要（不含密钥明文）。"""
    kb_cfg = get_kb_config()
    return {
        "enabled": kb_cfg.enabled,
        "observe_endpoint_available": True,
        "has_embed_api_key": bool(kb_cfg.embed_api_key),
        "kb_federation_observe_enabled": _kb_federation_observe_enabled(),
        "kb_federation_observe_endpoint_available": True,
        "kb_federation_store_enabled": kb_federation_store_enabled_from_env(),
        "kb_federation_store_backend": kb_federation_store_backend_from_env(),
        "kb_federation_store_admin_available": True,
    }


def _require_kb_federation_store_enabled() -> None:
    if not kb_federation_store_enabled_from_env():
        raise HTTPException(status_code=404, detail={"code": "KB_FEDERATION_STORE_DISABLED"})


def _kb_fed_store_http_detail(exc: KbFederationStoreError) -> dict[str, Any]:
    return {"code": exc.code, "message": exc.message}


def _orch_trace_guard(
    authorization: Annotated[str | None, Header()] = None,
    x_orch_trace_token: Annotated[str | None, Header(alias="X-Orch-Trace-Token")] = None,
) -> None:
    require_orch_trace_token_if_set(authorization, x_orch_trace_token)


async def _resolve_target_context_for_trace(task_id: str) -> tuple[dict[str, Any], str]:
    if task_id in _TASKS:
        return dict(_TASKS[task_id].target_context), "memory"
    rec = await _TASK_STORE.get_task(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")
    ck = await load_checkpoint_remote(task_id)
    if isinstance(ck, dict):
        ctx = ck.get("target_context")
        if isinstance(ctx, dict):
            return ctx, "checkpoint"
    return {}, "store_only"


async def _try_resolve_target_context(task_id: str) -> tuple[dict[str, Any], str] | None:
    """
    管理面观测用：任务不存在时返回 None（不抛 404），避免 V1 overview 默认 demo task_id 误伤。
    """
    tid = (task_id or "").strip()
    if not tid:
        return None
    if tid in _TASKS:
        return dict(_TASKS[tid].target_context), "memory"
    rec = await _TASK_STORE.get_task(tid)
    if not rec:
        return None
    ck = await load_checkpoint_remote(tid)
    if isinstance(ck, dict):
        ctx = ck.get("target_context")
        if isinstance(ctx, dict):
            return ctx, "checkpoint"
    return {}, "store_only"


async def _ensure_task_exists_for_trace(task_id: str) -> None:
    if task_id in _TASKS:
        return
    rec = await _TASK_STORE.get_task(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")


def _parse_execution_fields_or_400(fields_raw: str | None) -> set[str] | None:
    try:
        return parse_execution_fields_param(fields_raw)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNKNOWN_EXECUTION_FIELDS", "message": str(e)},
        ) from e


@app.post("/v1/orchestrator/tasks", status_code=204)
async def create_task(payload: CreateTaskPayload) -> None:
    """
    初始化任务的编排状态。
    当前实现为内存存储，后续可替换为 Redis / DB。
    """
    # 若 TaskStore 中已存在则直接返回（幂等）
    existing = await _TASK_STORE.get_task(payload.taskId)
    if existing:
        # 内存缓存中若不存在，则用 TaskRecord 构造 TaskState 视图
        if payload.taskId not in _TASKS:
            _TASKS[payload.taskId] = TaskState.from_task_record(existing)
        return

    record = TaskRecord(
        task_id=payload.taskId,
        name=payload.name,
        target=payload.target,
        description=payload.description,
        business_background=payload.businessBackground,
        extra_user_requirements=payload.extraUserRequirements,
    )
    await _TASK_STORE.create_task(record)
    _TASKS[payload.taskId] = TaskState.from_task_record(record)


@app.get("/v1/orchestrator/tasks/{task_id}", response_model=OrchestratorTaskStateResponse)
async def get_task(task_id: str) -> OrchestratorTaskStateResponse:
    """查询指定任务当前的编排状态。"""
    state = _TASKS.get(task_id)
    if state is None:
        record = await _TASK_STORE.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="task not found")
        state = TaskState.from_task_record(record)
        _TASKS[task_id] = state
    return state.to_response()


@app.get("/v1/orchestrator/tasks/{task_id}/todos")
async def get_task_todos(task_id: str) -> list[dict[str, Any]]:
    """v1：从 TaskStore 返回该任务的 Todo 列表，字段 camelCase 与 API 对齐。"""
    record = await _TASK_STORE.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="task not found")
    todos = await _TASK_STORE.list_todos(task_id)
    return [
        {
            "todoId": r.todo_id,
            "name": r.name,
            "target": r.target,
            "phase": r.phase,
            "status": r.status,
            "description": r.description or "",
        }
        for r in todos
    ]


class ChunkWriteRequest(BaseModel):
    chunk_type: str
    body: Any
    tenant_id: str | None = None
    retention: str | None = None
    ttl_seconds: int | None = None


class ChunkBatchGetRequest(BaseModel):
    chunk_ids: list[str]


class ChunkAdjustRefsRequest(BaseModel):
    chunk_ids: list[str]
    delta: int = 1


class ChunkSetRetentionRequest(BaseModel):
    chunk_id: str
    retention: str


class ChunkGcSweepRequest(BaseModel):
    task_id: str | None = None


class KbFederationMetaUpsertBody(BaseModel):
    task_id: str
    plan_item_id: str | None = None
    agent_id: str | None = None
    phase: str | None = None
    capability: str | list[str] | None = None
    kb_entry_id: str | None = None
    chunk_ref: str | None = None
    artifact_ref: str | None = None
    parent_ref: str | None = None
    content_type: str | None = None
    summary: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _header_tenant(x_tenant_id: str | None) -> str | None:
    if x_tenant_id is None:
        return None
    s = x_tenant_id.strip()
    return s or None


@app.post("/v1/orchestrator/tasks/{task_id}/chunks")
async def create_task_chunk(task_id: str, req: ChunkWriteRequest) -> dict[str, str]:
    """写入 chunk，返回 chunk_id。"""
    try:
        cid = write_chunk(
            task_id,
            chunk_type=req.chunk_type,
            body=req.body,
            tenant_id=req.tenant_id,
            retention=req.retention or "ephemeral",
            ttl_seconds=req.ttl_seconds,
        )
    except ChunkStoreError as e:
        raise HTTPException(status_code=e.http_status, detail=chunk_store_error_http_detail(e)) from e
    return {"chunk_id": cid}


@app.get("/v1/orchestrator/tasks/{task_id}/chunks/{chunk_id}")
async def get_task_chunk(
    task_id: str,
    chunk_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> dict[str, Any]:
    """按 id 读取单个 chunk。若 chunk 绑定 tenant_id，应传 X-Tenant-Id 且一致，否则 403。"""
    tenant = _header_tenant(x_tenant_id)
    try:
        rec = read_chunk(
            task_id,
            chunk_id,
            expect_tenant_id=tenant,
            require_tenant_when_bound=True,
        )
    except ChunkStoreError as e:
        raise HTTPException(status_code=e.http_status, detail=chunk_store_error_http_detail(e)) from e
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail={
                "structured_error": {
                    "kind": "chunk_store",
                    "code": "CHUNK_NOT_FOUND",
                    "message": "chunk not found",
                    "details": {"task_id": task_id, "chunk_id": chunk_id},
                }
            },
        )
    return rec


@app.post("/v1/orchestrator/tasks/{task_id}/chunks:batchGet")
async def batch_get_task_chunks(
    task_id: str,
    req: ChunkBatchGetRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> dict[str, Any]:
    """批量读取 chunk；缺失、非法 id 或租户不匹配对应值为 null。"""
    ids = req.chunk_ids or []
    if len(ids) > CHUNK_BATCH_GET_MAX:
        raise HTTPException(
            status_code=400,
            detail={
                "structured_error": {
                    "kind": "chunk_store",
                    "code": "CHUNK_BATCH_TOO_MANY",
                    "message": f"chunk_ids too many (max {CHUNK_BATCH_GET_MAX})",
                    "details": {"max": CHUNK_BATCH_GET_MAX, "got": len(ids)},
                }
            },
        )
    tenant = _header_tenant(x_tenant_id)
    try:
        chunks = read_chunks_batch(
            task_id,
            ids,
            expect_tenant_id=tenant,
            require_tenant_when_bound=True,
        )
    except ChunkStoreError as e:
        raise HTTPException(status_code=e.http_status, detail=chunk_store_error_http_detail(e)) from e
    return {"chunks": chunks}


@app.get("/v1/orchestrator/chunk-store/metrics")
async def get_chunk_store_metrics() -> dict[str, Any]:
    """进程内 Chunk 存储计数快照（排障/验收）；含 KB 嵌入配额计数（nf-perf-quotas-embed-chunk）。"""
    from app.kb_embed_quota_metrics import snapshot as embed_quota_snapshot

    return {
        "chunk_store_metrics": chunk_store_metrics_snapshot(),
        "kb_embed_quota_metrics": embed_quota_snapshot(),
    }


@app.post("/v1/orchestrator/kb/manual:ingest")
async def kb_manual_knowledge_ingest(
    req: ManualKnowledgeIngestBody,
    authorization: str | None = Header(default=None),
    x_kb_manual_ingest_token: str | None = Header(default=None, alias="X-KB-Manual-Ingest-Token"),
) -> dict[str, Any]:
    """
    kb-r2b：Manual 静态知识写入 Qdrant（需 `KB_MANUAL_INGEST_TOKEN`）。
    鉴权：`Authorization: Bearer <token>` 或 `X-KB-Manual-Ingest-Token`。
    """
    require_kb_manual_ingest_token(authorization, x_kb_manual_ingest_token)
    return await run_manual_knowledge_ingest(req)


@app.post("/v1/orchestrator/kb/manual:approve")
async def kb_manual_knowledge_approve(
    req: ManualKnowledgeApproveBody,
    authorization: str | None = Header(default=None),
    x_kb_manual_ingest_token: str | None = Header(default=None, alias="X-KB-Manual-Ingest-Token"),
) -> dict[str, Any]:
    """将 `review_status=pending` 的 manual 知识审核为 approved。"""
    require_kb_manual_ingest_token(authorization, x_kb_manual_ingest_token)
    return await run_manual_knowledge_approve(req)


@app.post("/v1/orchestrator/kb/pipeline:cveSync")
async def kb_pipeline_cve_sync_http(
    authorization: str | None = Header(default=None),
    x_kb_manual_ingest_token: str | None = Header(default=None, alias="X-KB-Manual-Ingest-Token"),
) -> dict[str, Any]:
    """
    kb-r2c：按需拉取 NVD CVE 并 upsert（不受 `KB_PIPELINE_CVE_ENABLED` 限制）。
    鉴权：`KB_PIPELINE_ADMIN_TOKEN` 或 `KB_MANUAL_INGEST_TOKEN`；头与 Manual 接口相同。
    """
    require_kb_pipeline_admin_token(authorization, x_kb_manual_ingest_token)
    from app.kb_pipeline_runners import run_cve_nvd_sync_once

    return await run_cve_nvd_sync_once(force=True)


@app.post("/v1/orchestrator/kb/pipeline:blogFetch")
async def kb_pipeline_blog_fetch_http(
    authorization: str | None = Header(default=None),
    x_kb_manual_ingest_token: str | None = Header(default=None, alias="X-KB-Manual-Ingest-Token"),
) -> dict[str, Any]:
    """按需抓取 `KB_BLOG_SOURCE_URLS` 并入库（不受 `KB_PIPELINE_BLOG_ENABLED` 限制）。"""
    require_kb_pipeline_admin_token(authorization, x_kb_manual_ingest_token)
    from app.kb_pipeline_runners import run_blog_fetch_once

    return await run_blog_fetch_once(force=True)


@app.get("/v1/orchestrator/skills/{skill_id}/pack", response_model=SkillPack)
async def get_skill_pack(skill_id: str) -> SkillPack:
    """
    加载技能包为 Compiler 输入结构（SKILL.md + _meta.json + tools_registry 条目）。
    """
    try:
        return load_skill_pack(skill_id)
    except SkillPackLoadError as e:
        raise HTTPException(status_code=e.http_status, detail=skill_pack_error_http_detail(e)) from e


@app.post("/v1/orchestrator/tasks/{task_id}/chunks:adjustRefs")
async def adjust_task_chunk_refs(task_id: str, req: ChunkAdjustRefsRequest) -> dict[str, Any]:
    """为 Plan 等持有方增减 ref_count；仅当 ref_count=0 且 ephemeral 过期时 GC 可删。"""
    ids = req.chunk_ids or []
    if len(ids) > max_ref_adjust_batch():
        raise HTTPException(
            status_code=400,
            detail={
                "structured_error": {
                    "kind": "chunk_store",
                    "code": "CHUNK_REF_BATCH_TOO_MANY",
                    "message": f"chunk_ids too many (max {max_ref_adjust_batch()})",
                    "details": {"max": max_ref_adjust_batch(), "got": len(ids)},
                }
            },
        )
    results: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    for raw_id in ids:
        try:
            new_rc = adjust_chunk_ref(task_id, raw_id, req.delta)
            results[raw_id.strip()] = new_rc
        except ChunkStoreError as e:
            errors.append({"chunk_id": raw_id, "code": e.code, "message": e.message})
    return {"ref_by_chunk_id": results, "errors": errors}


@app.post("/v1/orchestrator/tasks/{task_id}/chunks:setRetention")
async def set_task_chunk_retention(task_id: str, req: ChunkSetRetentionRequest) -> dict[str, str]:
    """切换 pinned / proven / ephemeral（回 ephemeral 时按当前时间重写 expires_at）。"""
    try:
        set_chunk_retention(task_id, req.chunk_id, req.retention)
    except ChunkStoreError as e:
        raise HTTPException(status_code=e.http_status, detail=chunk_store_error_http_detail(e)) from e
    return {"ok": "true", "chunk_id": req.chunk_id, "retention": req.retention}


@app.post("/v1/orchestrator/chunk-store/gc:sweep")
async def post_chunk_gc_sweep(req: ChunkGcSweepRequest | None = None) -> dict[str, Any]:
    """
    同步触发 GC（供 cron/运维）；task_id 为空则扫描整个 WORKSPACE_ROOT。
    """
    if not gc_enabled():
        return {"gc_enabled": False, "message": "CHUNK_GC_ENABLED is off"}
    payload = req or ChunkGcSweepRequest(task_id=None)
    if payload.task_id:
        try:
            return {"gc_enabled": True, **gc_sweep_task(payload.task_id)}
        except ChunkStoreError as e:
            raise HTTPException(status_code=e.http_status, detail=chunk_store_error_http_detail(e)) from e
    return {"gc_enabled": True, **gc_sweep_all_tasks()}


class RestoreTaskPayload(BaseModel):
    """恢复任务时若无 checkpoint，用 DB 的元数据在编排器内重建状态（仅重启后断点重连用）。"""
    name: str | None = None
    target: str | None = None
    description: str | None = None
    businessBackground: str | None = None
    extraUserRequirements: str | None = None


@app.post("/v1/orchestrator/tasks/{task_id}/restore", response_model=OrchestratorTaskStateResponse)
async def restore_task(task_id: str, payload: RestoreTaskPayload | None = None) -> OrchestratorTaskStateResponse:
    """
    断点重连：编排器重启后 _TASKS 为空，run/tick 会 404。
    先调本接口：若有 checkpoint 则从Evidence恢复；若无则用 payload（由Gateway从 DB 传入）在编排器内重建最小状态，之后即可 run/tick。
    """
    state = _TASKS.get(task_id)
    if state:
        return state.to_response()
    store_rec = await _TASK_STORE.get_task(task_id)
    checkpoint = None
    try:
        checkpoint = await load_checkpoint_remote(task_id)
    except Exception:
        pass
    if checkpoint:
        rc = checkpoint.get("target_context") or {}
        state = TaskState(
            task_id=task_id,
            name=checkpoint.get("name") or "resumed",
            target=checkpoint.get("target") or "",
            description=checkpoint.get("description"),
            business_background=rc.get("business_background") if isinstance(rc.get("business_background"), str) else None,
            extra_user_requirements=rc.get("extra_user_requirements") if isinstance(rc.get("extra_user_requirements"), str) else None,
        )
        _TASKS[task_id] = state
        try:
            ph = Phase(checkpoint.get("current_phase") or "RECON")
        except ValueError:
            ph = Phase.RECON
        hydrate_phase_clock_from_checkpoint_and_store(
            state, checkpoint, phase=ph, store_record=store_rec
        )
        restored_context = dict(rc)
        if "target" not in restored_context:
            restored_context["target"] = state.target
        if "task_background" not in restored_context:
            restored_context["task_background"] = f"本任务为经授权的渗透测试，仅对 {state.target} 进行安全测试，禁止越权。"
        state.target_context = restored_context
        state.history_summary = checkpoint.get("history_summary") or ""
        state.coverage_attempted = state.target_context.get("_coverage_attempted") or []
        state.status = TaskStatus.RUNNING
        state.stop_requested = False
        state.updated_at = datetime.utcnow()
        # 用最新 TaskState 回写 TaskStore
        await _TASK_STORE.update_task(state.to_task_record())
        return state.to_response()
    if payload and (payload.target or "").strip():
        state = TaskState(
            task_id=task_id,
            name=(payload.name or "").strip() or "未命名任务",
            target=(payload.target or "").strip(),
            description=payload.description,
            business_background=payload.businessBackground,
            extra_user_requirements=payload.extraUserRequirements,
        )
        _TASKS[task_id] = state
        await _TASK_STORE.create_task(state.to_task_record())
        return state.to_response()
    raise HTTPException(
        status_code=404,
        detail="task not in orchestrator memory and no checkpoint; call restore with body { name, target, description, businessBackground, extraUserRequirements } from DB",
    )


async def _emit_trace(event: TraceEvent) -> None:
    await emit_trace(event)


async def _emit_task_paused(
    state: TaskState,
    *,
    reason_code: str,
    reason: str,
    elapsed_seconds: float | None = None,
    max_duration_seconds: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "phase": state.current_phase.value,
        "reason_code": reason_code,
        "reason": reason,
    }
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = round(float(elapsed_seconds), 3)
    if max_duration_seconds is not None:
        payload["max_duration_seconds"] = int(max_duration_seconds)
    try:
        await _emit_trace(
            TraceEvent(
                task_id=state.task_id,
                timestamp=datetime.utcnow().isoformat() + "Z",
                event_type="TASK_PAUSED",
                source_module="orchestrator",
                payload=payload,
            )
        )
    except Exception as exc:
        logger.warning("emit TASK_PAUSED failed: %s", exc)


async def _emit_task_resumed(state: TaskState, *, reason: str) -> None:
    try:
        await _emit_trace(
            TraceEvent(
                task_id=state.task_id,
                timestamp=datetime.utcnow().isoformat() + "Z",
                event_type="TASK_RESUMED",
                source_module="orchestrator",
                payload={
                    "phase": state.current_phase.value,
                    "reason": reason,
                },
            )
        )
    except Exception as exc:
        logger.warning("emit TASK_RESUMED failed: %s", exc)


class RunStatusResponse(BaseModel):
    taskId: str
    running: bool
    maxTicks: int = 0
    maxDurationSeconds: int = 0
    startedAt: str | None = None
    finishedAt: str | None = None
    error: str | None = None


async def _run_loop(
    state: TaskState,
    max_ticks: int,
    max_duration_seconds: int,
    *,
    use_manager_agent: bool = False,
) -> None:
    run_start = time.perf_counter()
    max_outer = max(1, int(os.getenv("ORCHESTRATOR_LLM_OUTER_RETRIES", "12")))
    base_sleep = float(os.getenv("ORCHESTRATOR_LLM_OUTER_RETRY_BASE_SECONDS", "1.0"))

    for _ in range(max_ticks):
        if state.is_terminal():
            break
        if state.stop_requested or state.status == TaskStatus.PAUSED:
            break
        elapsed_seconds = time.perf_counter() - run_start
        if max_duration_seconds > 0 and elapsed_seconds >= max_duration_seconds:
            state.status = TaskStatus.PAUSED
            state.stop_requested = True
            state.updated_at = datetime.utcnow()
            try:
                await save_checkpoint_remote(
                    task_id=state.task_id,
                    current_phase=state.current_phase.value,
                    status=state.status.value,
                    target_context=state.target_context,
                    history_summary=state.history_summary,
                    name=state.name,
                    target=state.target,
                    description=state.description,
                    **_checkpoint_timing_kwargs(state),
                )
            except Exception as e:
                logger.warning("save checkpoint on max_duration failed: %s", e)
            await _emit_task_paused(
                state,
                reason_code="RUN_DURATION_LIMIT",
                reason="单次运行时长达到上限，已保存断点；续跑后会从当前阶段继续。",
                elapsed_seconds=elapsed_seconds,
                max_duration_seconds=max_duration_seconds,
            )
            break
        transient_attempt = 0
        while True:
            try:
                t_tick = time.perf_counter()
                if use_manager_agent:
                    await state_machine_tick_manager(
                        state,
                        enable_executor=ENABLE_EXECUTOR,
                        task_store=_TASK_STORE,
                    )
                else:
                    await state_machine_tick(state, enable_executor=ENABLE_EXECUTOR)
                record_tick_attempt(
                    ok=True,
                    duration_ms=(time.perf_counter() - t_tick) * 1000.0,
                )
                break
            except LLMCallFailed as exc:
                if not exc.transient:
                    record_tick_attempt(ok=False, duration_ms=0.0)
                    raise
                transient_attempt += 1
                if transient_attempt >= max_outer:
                    logger.error(
                        "LLM transient failures exhausted after %s outer retries: %s",
                        transient_attempt,
                        exc.detail,
                    )
                    record_tick_attempt(ok=False, duration_ms=0.0)
                    raise
                delay = min(
                    120.0,
                    base_sleep * (2 ** (transient_attempt - 1)) + random.uniform(0.0, 0.3),
                )
                logger.warning(
                    "LLM transient failure (outer %s/%s), retrying same tick after %.2fs: %s",
                    transient_attempt,
                    max_outer,
                    delay,
                    (exc.detail or "")[:400],
                )
                try:
                    await _emit_trace(
                        TraceEvent(
                            task_id=state.task_id,
                            timestamp=datetime.utcnow().isoformat() + "Z",
                            event_type="LLM_TRANSIENT_RETRY",
                            source_module="orchestrator",
                            payload={
                                "phase": state.current_phase.value,
                                "attempt": transient_attempt,
                                "max_outer": max_outer,
                                "detail": (exc.detail or "")[:2000],
                            },
                        )
                    )
                except Exception:
                    pass
                await asyncio.sleep(delay)
            except Exception:
                record_tick_attempt(ok=False, duration_ms=0.0)
                raise
        if state.stop_requested:
            state.status = TaskStatus.PAUSED
            try:
                await save_checkpoint_remote(
                    task_id=state.task_id,
                    current_phase=state.current_phase.value,
                    status=state.status.value,
                    target_context=state.target_context,
                    history_summary=state.history_summary,
                    name=state.name,
                    target=state.target,
                    description=state.description,
                    **_checkpoint_timing_kwargs(state),
                )
            except Exception as e:
                logger.warning("save checkpoint on stop_requested failed: %s", e)
            break


def _to_run_status(task_id: str) -> RunStatusResponse:
    job = _RUN_JOBS.get(task_id) or {}
    return RunStatusResponse(
        taskId=task_id,
        running=bool(job.get("running")),
        maxTicks=int(job.get("max_ticks") or 0),
        maxDurationSeconds=int(job.get("max_duration_seconds") or 0),
        startedAt=job.get("started_at"),
        finishedAt=job.get("finished_at"),
        error=job.get("error"),
    )


@app.post("/v1/orchestrator/tasks/{task_id}/tick", response_model=OrchestratorTaskStateResponse)
async def tick_task(task_id: str) -> OrchestratorTaskStateResponse:
    """
    单步推进任务状态机（一次 tick）。
    由 state_machine 完成「决策 → 执行 → 留痕」闭环。
    """
    state = _TASKS.get(task_id)
    if state is None:
        record = await _TASK_STORE.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="task not found")
        state = TaskState.from_task_record(record)
        _TASKS[task_id] = state

    if state.is_terminal():
        return state.to_response()
    if state.status == TaskStatus.PAUSED:
        return state.to_response()

    owner_id = f"tick:{os.getpid()}:{task_id}"
    locked = await _TASK_STORE.acquire_task_lock(task_id, owner_id, ttl_seconds=30)
    if not locked:
        raise HTTPException(status_code=429, detail="task is locked by another orchestrator instance")

    try:
        try:
            t_tick = time.perf_counter()
            if ENABLE_MANAGER_AGENT:
                await state_machine_tick_manager(
                    state,
                    enable_executor=ENABLE_EXECUTOR,
                    task_store=_TASK_STORE,
                )
            else:
                await state_machine_tick(state, enable_executor=ENABLE_EXECUTOR)
            record_tick_attempt(
                ok=True,
                duration_ms=(time.perf_counter() - t_tick) * 1000.0,
            )
        except LLMCallFailed as exc:
            record_tick_attempt(ok=False, duration_ms=0.0)
            err_msg = exc.detail
            logger.exception("tick failed (LLM): %s", err_msg)
            await _emit_trace(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=datetime.utcnow().isoformat() + "Z",
                    event_type="ERROR",
                    source_module="orchestrator",
                    payload={
                        "phase": state.current_phase.value,
                        "message": "tick failed (LLM decision)",
                        "detail": err_msg,
                    },
                )
            )
            raise HTTPException(status_code=exc.status_code, detail=err_msg) from exc
        except (httpx.HTTPError, HTTPException) as exc:
            record_tick_attempt(ok=False, duration_ms=0.0)
            err_msg = str(exc)
            logger.exception("tick failed: %s", err_msg)
            await _emit_trace(
                TraceEvent(
                    task_id=state.task_id,
                    timestamp=datetime.utcnow().isoformat() + "Z",
                    event_type="ERROR",
                    source_module="orchestrator",
                    payload={
                        "phase": state.current_phase.value,
                        "message": "tick failed (decision or executor)",
                        "detail": err_msg,
                    },
                )
            )
            status = 502 if isinstance(exc, httpx.HTTPError) else (exc.status_code if hasattr(exc, "status_code") else 500)
            raise HTTPException(status_code=status, detail=err_msg) from exc

        # 将最新状态写回 TaskStore
        await _TASK_STORE.update_task(state.to_task_record())
        return state.to_response()
    finally:
        await _TASK_STORE.release_task_lock(task_id, owner_id)


def _default_run_max_duration_seconds() -> int:
    """单次 run 默认墙钟上限；可通过 ORCH_DEFAULT_RUN_MAX_DURATION_SECONDS 覆盖，0 表示不限制。"""
    raw = (os.getenv("ORCH_DEFAULT_RUN_MAX_DURATION_SECONDS") or "900").strip()
    try:
        v = int(raw)
    except ValueError:
        v = 900
    return max(0, v)


@app.post("/v1/orchestrator/tasks/{task_id}/run", response_model=OrchestratorTaskStateResponse)
async def run_task(
    task_id: str,
    max_ticks: int = 100,
    max_duration_seconds: int | None = None,
) -> OrchestratorTaskStateResponse:
    """
    编排循环：反复 tick 直到任务进入终态（DONE/FAILED）、达到 max_ticks 或超时。
    max_duration_seconds > 0 时，超过则写断点并置 PAUSED，避免长跑占满资源。
    未传 max_duration_seconds 时默认 900 秒（15 分钟）；显式传 0 表示不限制本次墙钟时长。
    """
    if max_duration_seconds is None:
        max_duration_seconds = _default_run_max_duration_seconds()
    state = _TASKS.get(task_id)
    if state is None:
        record = await _TASK_STORE.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="task not found")
        state = TaskState.from_task_record(record)
        _TASKS[task_id] = state
    if state.is_terminal():
        return state.to_response()

    existing = _RUN_JOBS.get(task_id)
    if existing and existing.get("running"):
        return state.to_response()

    if MAX_CONCURRENT_TASKS_RUNNING > 0:
        running_count = sum(1 for j in _RUN_JOBS.values() if j.get("running"))
        if running_count >= MAX_CONCURRENT_TASKS_RUNNING:
            raise HTTPException(
                status_code=429,
                detail=f"max concurrent running tasks reached ({MAX_CONCURRENT_TASKS_RUNNING})",
            )

    owner_id = f"run:{os.getpid()}:{task_id}"
    locked = await _TASK_STORE.acquire_task_lock(task_id, owner_id, ttl_seconds=max(max_duration_seconds or 60, 60))
    if not locked:
        raise HTTPException(status_code=429, detail="task is locked by another orchestrator instance")

    state.stop_requested = False
    _RUN_JOBS[task_id] = {
        "running": True,
        "max_ticks": max_ticks,
        "max_duration_seconds": max_duration_seconds,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "finished_at": None,
        "error": None,
    }

    async def _runner() -> None:
        try:
            try:
                await _run_loop(
                    state,
                    max_ticks=max_ticks,
                    max_duration_seconds=max_duration_seconds,
                    use_manager_agent=ENABLE_MANAGER_AGENT,
                )
            except LLMCallFailed as exc:
                err_msg = exc.detail
                logger.exception("run loop failed (LLM): %s", err_msg)
                state.status = TaskStatus.FAILED
                state.updated_at = datetime.utcnow()
                await _emit_trace(
                    TraceEvent(
                        task_id=state.task_id,
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        event_type="ERROR",
                        source_module="orchestrator",
                        payload={
                            "phase": state.current_phase.value,
                            "message": "run loop failed (LLM decision)",
                            "detail": err_msg,
                        },
                    )
                )
                try:
                    await save_checkpoint_remote(
                        task_id=state.task_id,
                        current_phase=state.current_phase.value,
                        status=state.status.value,
                        target_context=state.target_context,
                        history_summary=state.history_summary,
                        name=state.name,
                        target=state.target,
                        description=state.description,
                        **_checkpoint_timing_kwargs(state),
                    )
                except Exception:
                    pass
                await _TASK_STORE.update_task(state.to_task_record())
                _RUN_JOBS[task_id]["error"] = err_msg
            except (httpx.HTTPError, HTTPException) as exc:
                err_msg = str(exc)
                logger.exception("run tick failed: %s", err_msg)
                state.status = TaskStatus.FAILED
                state.updated_at = datetime.utcnow()
                await _emit_trace(
                    TraceEvent(
                        task_id=state.task_id,
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        event_type="ERROR",
                        source_module="orchestrator",
                        payload={
                            "phase": state.current_phase.value,
                            "message": "run loop tick failed",
                            "detail": err_msg,
                        },
                    )
                )
                try:
                    await save_checkpoint_remote(
                        task_id=state.task_id,
                        current_phase=state.current_phase.value,
                        status=state.status.value,
                        target_context=state.target_context,
                        history_summary=state.history_summary,
                        name=state.name,
                        target=state.target,
                        description=state.description,
                        **_checkpoint_timing_kwargs(state),
                    )
                except Exception:
                    pass
                # 异常结束时同步 TaskStore
                await _TASK_STORE.update_task(state.to_task_record())
                _RUN_JOBS[task_id]["error"] = err_msg
            except Exception as exc:
                err_msg = str(exc)
                logger.exception("run loop unexpected error: %s", err_msg)
                state.status = TaskStatus.FAILED
                state.updated_at = datetime.utcnow()
                _RUN_JOBS[task_id]["error"] = err_msg
            finally:
                _RUN_JOBS[task_id]["running"] = False
                _RUN_JOBS[task_id]["finished_at"] = datetime.utcnow().isoformat() + "Z"
                # run 结束后同步 TaskStore
                await _TASK_STORE.update_task(state.to_task_record())
        finally:
            await _TASK_STORE.release_task_lock(task_id, owner_id)

    asyncio.create_task(_runner())
    return state.to_response()


@app.get("/v1/orchestrator/tasks/{task_id}/run-status", response_model=RunStatusResponse)
async def get_run_status(task_id: str) -> RunStatusResponse:
    if task_id not in _TASKS:
        raise HTTPException(status_code=404, detail="task not found")
    return _to_run_status(task_id)


@app.post("/v1/orchestrator/tasks/{task_id}/stop", response_model=OrchestratorTaskStateResponse)
async def stop_task(task_id: str) -> OrchestratorTaskStateResponse:
    """停止任务：保存断点到Evidence，并标记为 PAUSED。若正在 run 循环，循环会在下一轮检查 stop_requested 后退出并写断点。"""
    state = _TASKS.get(task_id)
    if state is None:
        record = await _TASK_STORE.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="task not found")
        state = TaskState.from_task_record(record)
        _TASKS[task_id] = state
    if state.is_terminal():
        return state.to_response()

    state.stop_requested = True
    state.status = TaskStatus.PAUSED
    state.updated_at = datetime.utcnow()
    try:
        await save_checkpoint_remote(
            task_id=state.task_id,
            current_phase=state.current_phase.value,
            status=state.status.value,
            target_context=state.target_context,
            history_summary=state.history_summary,
            name=state.name,
            target=state.target,
            description=state.description,
            **_checkpoint_timing_kwargs(state),
        )
    except Exception as e:
        logger.warning("save checkpoint on stop failed: %s", e)
    # stop 后同步 TaskStore
    await _TASK_STORE.update_task(state.to_task_record())
    await _emit_task_paused(
        state,
        reason_code="USER_STOPPED",
        reason="用户请求暂停，已保存断点。",
    )
    return state.to_response()


@app.post("/v1/orchestrator/tasks/{task_id}/resume", response_model=OrchestratorTaskStateResponse)
async def resume_task(task_id: str) -> OrchestratorTaskStateResponse:
    """从断点恢复：从Evidence读取 checkpoint，恢复状态并置为 RUNNING，可再次 tick/run。"""
    state = _TASKS.get(task_id)
    store_rec = await _TASK_STORE.get_task(task_id)
    try:
        checkpoint = await load_checkpoint_remote(task_id)
    except Exception:
        raise
    if not checkpoint:
        if not state:
            raise HTTPException(status_code=404, detail="task not found and no checkpoint")
        if state.status != TaskStatus.PAUSED:
            return state.to_response()
        state.status = TaskStatus.RUNNING
        state.stop_requested = False
        _reset_phase_clock_for_resume(state)
        state.updated_at = datetime.utcnow()
        await _TASK_STORE.update_task(state.to_task_record())
        await _emit_task_resumed(state, reason="用户请求续跑，阶段计时已从当前时间重新开始。")
        return state.to_response()

    # 用 checkpoint 恢复或创建 state
    if state is None:
        rc = checkpoint.get("target_context") or {}
        state = TaskState(
            task_id=task_id,
            name=checkpoint.get("name") or "resumed",
            target=checkpoint.get("target") or "",
            description=checkpoint.get("description"),
            business_background=rc.get("business_background") if isinstance(rc.get("business_background"), str) else None,
            extra_user_requirements=rc.get("extra_user_requirements") if isinstance(rc.get("extra_user_requirements"), str) else None,
        )
        _TASKS[task_id] = state
    try:
        ph = Phase(checkpoint.get("current_phase") or "RECON")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid current_phase: {checkpoint.get('current_phase')}") from e
    hydrate_phase_clock_from_checkpoint_and_store(
        state, checkpoint, phase=ph, store_record=store_rec
    )
    restored_context = checkpoint.get("target_context") or {}
    if "target" not in restored_context:
        restored_context["target"] = state.target
    if "task_background" not in restored_context:
        restored_context["task_background"] = f"本任务为经授权的渗透测试，仅对 {state.target} 进行安全测试，禁止越权。"
    state.target_context = restored_context
    _bg = restored_context.get("business_background")
    if isinstance(_bg, str):
        state.business_background = _bg
    _ex = restored_context.get("extra_user_requirements")
    if isinstance(_ex, str):
        state.extra_user_requirements = _ex
    state.history_summary = checkpoint.get("history_summary") or ""
    state.coverage_attempted = state.target_context.get("_coverage_attempted") or []
    state.status = TaskStatus.RUNNING
    state.stop_requested = False
    _reset_phase_clock_for_resume(state)
    state.updated_at = datetime.utcnow()
    await _TASK_STORE.update_task(state.to_task_record())
    await _emit_task_resumed(state, reason="用户请求续跑，已从 checkpoint 恢复并重置阶段计时。")
    return state.to_response()


@app.get("/health")
async def health() -> dict:
    v1_mode = (os.getenv("V1_SCHEDULING_POLICY") or "capability_first").strip().lower()
    return {
        "status": "ok",
        "orch_plan_mode_enabled": orch_plan_mode_enabled(),
        "v1_agent_registry": _V1_AGENT_REGISTRY.summary(),
        "v1_scheduling": {
            "mode": v1_mode,
            "observe_endpoint_available": True,
            "role_filter_enabled": scheduling_role_filter_enabled(),
            "plan_item_dispatch_enabled": _planitem_dispatch_enabled(),
        },
        "v1_mq_lanes": v1_mq_lane_health_summary(),
        "v1_kb": _v1_kb_health_summary(),
    }


@app.get("/v1/orchestrator/sli/snapshot")
async def orchestrator_sli_snapshot(include_mq: bool = True) -> dict[str, Any]:
    """
    R8a：编译/tick 进程内 SLI 计数、可选 MQ 队列深度、环境阈值告警评估（供监控拉取）。
    """
    from app.orchestrator_sli_snapshot import build_sli_snapshot

    return await build_sli_snapshot(include_mq=include_mq)


@app.get("/v1/orchestrator/admin/v1/scheduling-observe")
async def get_v1_scheduling_observe(
    phase: str = "RECON",
    task_id: str = "observe-demo-task",
    preferred_capability: str | None = None,
) -> dict[str, Any]:
    """
    v1-a7：只读观测端点，返回当前策略模式与候选选择示例。
    不改变任何调度行为，不接入主状态机。
    """
    request = SchedulingRequest(
        phase=(phase or "RECON").strip().upper(),
        task_id=(task_id or "observe-demo-task").strip(),
        preferred_capability=(preferred_capability or "").strip() or None,
    )
    enabled_agents = _V1_AGENT_REGISTRY.list_agents(enabled_only=True)
    selected = _V1_SCHEDULING_POLICY.choose(request, enabled_agents)
    mode = (os.getenv("V1_SCHEDULING_POLICY") or "capability_first").strip().lower()
    out: dict[str, Any] = {
        "mode": mode,
        "request": {
            "phase": request.phase,
            "task_id": request.task_id,
            "preferred_capability": request.preferred_capability,
        },
        "enabled_candidates": [a.agent_id for a in enabled_agents],
        "selected_candidates": [a.agent_id for a in selected],
        "role_filter_enabled": scheduling_role_filter_enabled(),
    }
    _attach_planitem_observe_fields(out)
    return out


@app.get("/v1/orchestrator/admin/v1/overview")
async def get_v1_overview(
    phase: str = "RECON",
    task_id: str = "overview-demo-task",
    preferred_capability: str | None = None,
) -> dict[str, Any]:
    """
    v1-a9：V1 能力只读总览。
    聚合 registry 摘要 + scheduling 观测 + KB 健康摘要，不改动运行时调度行为。
    """
    scheduling = await get_v1_scheduling_observe(
        phase=phase,
        task_id=task_id,
        preferred_capability=preferred_capability,
    )
    tid = (task_id or "").strip()
    tactical_block: dict[str, Any] = {"available": False, "items": [], "context_source": None}
    if tid:
        resolved = await _try_resolve_target_context(tid)
        if resolved is not None:
            ctx, src = resolved
            tactical_block = {
                "available": True,
                "context_source": src,
                "items": prepare_tactical_incremental_section_api(
                    ctx.get("_tactical_incremental_artifacts"),
                    apply_redact=True,
                ),
            }
    return {
        "v1_agent_registry": _V1_AGENT_REGISTRY.summary(),
        "v1_scheduling": scheduling,
        "v1_mq_lanes": v1_mq_lane_health_summary(),
        "v1_kb": _v1_kb_health_summary(),
        "v1_tactical_incremental": tactical_block,
    }


@app.get("/v1/orchestrator/admin/v1/kb-observe")
async def get_v1_kb_observe() -> dict[str, Any]:
    """
    v1-a20：V1 KB 只读观测端点。
    仅返回编排器侧 KB 配置摘要，不暴露密钥值，不改变运行时行为。
    """
    kb_cfg = get_kb_config()
    return {
        "enabled": kb_cfg.enabled,
        "qdrant_url": kb_cfg.qdrant_url,
        "top_k": kb_cfg.top_k,
        "knowledge_collection": kb_cfg.knowledge_collection,
        "experience_collection": kb_cfg.experience_collection,
        "experience_legacy_collection": kb_cfg.experience_legacy_collection,
        "static_tier_split": kb_cfg.static_tier_split,
        "knowledge_tier_collections": {
            "manual": kb_cfg.knowledge_manual_collection,
            "cve": kb_cfg.knowledge_cve_collection,
            "blogs": kb_cfg.knowledge_blogs_collection,
        },
        "embedding_model": kb_cfg.embedding_model,
        "embed_base_url": kb_cfg.embed_base_url,
        "has_embed_api_key": bool(kb_cfg.embed_api_key),
        "auto_create": kb_cfg.auto_create,
    }


@app.get("/v1/orchestrator/admin/v1/kb-federation-observe")
async def get_v1_kb_federation_observe(sample_limit: int = 8) -> dict[str, Any]:
    """
    v1-a113：KB 联邦只读观测 PoC。默认关闭（ENV），响应为小对象，不含密钥。
    """
    enabled = _kb_federation_observe_enabled()
    store_on = kb_federation_store_enabled_from_env()
    out: dict[str, Any] = {
        "kb_federation_observe_enabled": enabled,
        "stub": not store_on,
    }
    if enabled:
        if store_on:
            store = get_kb_federation_meta_store()
            by_phase = store.aggregate_phases()
            lim = max(1, min(sample_limit, 50))
            out["aggregate"] = {
                "tasks_tracked": store.distinct_task_count(),
                "entries_total": store.count_all(),
                "by_phase": {
                    "RECON": by_phase.get("RECON", 0),
                    "EXPLOIT": by_phase.get("EXPLOIT", 0),
                    "BYPASS": by_phase.get("BYPASS", 0),
                    "OTHER": by_phase.get("OTHER", 0),
                },
                "sample_limit": lim,
                "sample_entry_ids": store.sample_entry_ids(limit=lim),
            }
        else:
            out["aggregate"] = {
                "tasks_tracked": 0,
                "by_phase": {"RECON": 0, "EXPLOIT": 0, "BYPASS": 0},
                "note": "poc placeholder; federation store disabled",
            }
    return out


@app.post("/v1/orchestrator/admin/v1/kb-federation-meta")
async def upsert_v1_kb_federation_meta_create(body: KbFederationMetaUpsertBody) -> dict[str, Any]:
    _require_kb_federation_store_enabled()
    try:
        norm = normalize_federation_meta(body.model_dump(exclude_none=True))
        rec = get_kb_federation_meta_store().create(norm)
    except KbFederationStoreError as e:
        raise HTTPException(status_code=400, detail=_kb_fed_store_http_detail(e)) from e
    return rec.to_public_dict()


@app.get("/v1/orchestrator/admin/v1/kb-federation-meta/{entry_id}")
async def get_v1_kb_federation_meta(entry_id: str, task_id: str | None = None) -> dict[str, Any]:
    _require_kb_federation_store_enabled()
    eid = (entry_id or "").strip()
    if not eid:
        raise HTTPException(status_code=400, detail={"code": "INVALID_ENTRY_ID"})
    store = get_kb_federation_meta_store()
    if isinstance(task_id, str) and task_id.strip():
        rec = store.get_for_task(task_id.strip(), eid)
    else:
        rec = store.get(eid)
    if rec is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
    return rec.to_public_dict()


@app.put("/v1/orchestrator/admin/v1/kb-federation-meta/{entry_id}")
async def upsert_v1_kb_federation_meta_update(entry_id: str, body: KbFederationMetaUpsertBody) -> dict[str, Any]:
    _require_kb_federation_store_enabled()
    eid = (entry_id or "").strip()
    if not eid:
        raise HTTPException(status_code=400, detail={"code": "INVALID_ENTRY_ID"})
    try:
        norm = normalize_federation_meta(body.model_dump(exclude_none=True))
        rec = get_kb_federation_meta_store().update(eid, norm)
    except KbFederationStoreError as e:
        status = 404 if e.code == "NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail=_kb_fed_store_http_detail(e)) from e
    return rec.to_public_dict()


@app.delete("/v1/orchestrator/admin/v1/kb-federation-meta/{entry_id}")
async def delete_v1_kb_federation_meta(entry_id: str) -> dict[str, Any]:
    _require_kb_federation_store_enabled()
    eid = (entry_id or "").strip()
    if not eid:
        raise HTTPException(status_code=400, detail={"code": "INVALID_ENTRY_ID"})
    deleted = get_kb_federation_meta_store().delete(eid)
    return {"deleted": deleted}


@app.get("/v1/orchestrator/admin/v1/kb-federation-meta")
async def list_v1_kb_federation_meta_for_task(task_id: str, limit: int = 50) -> dict[str, Any]:
    _require_kb_federation_store_enabled()
    tid = (task_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail={"code": "TASK_ID_REQUIRED"})
    lim = max(1, min(limit, 200))
    rows = [r.to_public_dict() for r in get_kb_federation_meta_store().list_for_task(tid, limit=lim)]
    return {"task_id": tid, "items": rows, "count": len(rows)}


@app.get("/v1/orchestrator/admin/v1/health-overview")
async def get_v1_health_overview(
    phase: str = "RECON",
    task_id: str = "health-overview-demo-task",
    preferred_capability: str | None = None,
) -> dict[str, Any]:
    """
    v1-a28：V1 只读聚合观测端点。
    同时返回 health 摘要与 overview 摘要，便于联调快速比对，不改变运行时行为。
    """
    return {
        "request": {
            "phase": phase,
            "task_id": task_id,
            "preferred_capability": preferred_capability,
        },
        "health": await health(),
        "overview": await get_v1_overview(
            phase=phase,
            task_id=task_id,
            preferred_capability=preferred_capability,
        ),
    }


@app.get("/v1/orchestrator/admin/skills")
async def list_skills_admin(phase: str | None = None) -> dict[str, Any]:
    """
    管理面：代理 Executor /v1/skills，返回已注册技能列表（含 category 元数据）。
    可选 phase 参数过滤阶段可用技能。Executor 不可达时返回空列表并附 error 字段。
    """
    executor_url = os.getenv("EXECUTOR_BASE_URL", "http://localhost:18102")
    try:
        params: dict[str, Any] = {}
        if phase:
            params["phase"] = phase.strip().upper()
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{executor_url}/v1/skills", params=params, timeout=8.0)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"skill_ids": [], "skills": [], "error": str(e)[:120]}


@app.get("/v1/orchestrator/executions/{request_id}")
async def get_execution(request_id: str) -> dict:
    """
    查询某次执行的状态（含 worker_id，MQ 模式下可验证负载均衡）。
    """
    rec = await _TASK_STORE.get_execution_status(request_id)
    if not rec:
        raise HTTPException(status_code=404, detail="execution not found")
    return execution_record_to_dict(rec)


@app.get(
    "/v1/orchestrator/tasks/{task_id}/trace",
    dependencies=[Depends(_orch_trace_guard)],
)
async def get_task_trace(
    task_id: str,
    executions_limit: int = 50,
    executions_offset: int = 0,
    executions_fields: str | None = None,
    plan_omit_plan_content: bool = False,
    plan_include_chunk_refs: bool = True,
) -> dict:
    """
    R7a：聚合 plan / compile 占位 / execution 列表（compile 未落库时 entries 为空）。
    R7b：executions 支持 limit/offset/fields；plan 支持省略正文与 chunk 引用；默认脱敏。
    """
    fset = _parse_execution_fields_or_400(executions_fields)
    cap = clamp_execution_paging_limit(executions_limit)
    off = clamp_execution_paging_offset(executions_offset)
    ctx, source = await _resolve_target_context_for_trace(task_id)
    raw_plan = plan_section_from_context(ctx)
    raw_compile = compile_section_from_context(ctx)
    total = await _TASK_STORE.count_executions_for_task(task_id)
    exec_rows = await _TASK_STORE.list_executions_for_task(task_id, limit=cap, offset=off)
    next_off = off + len(exec_rows)
    has_more = next_off < total
    return {
        "task_id": task_id,
        "context_source": source,
        "plan": prepare_plan_section_api(
            raw_plan,
            omit_plan_content=plan_omit_plan_content,
            include_chunk_refs=plan_include_chunk_refs,
            apply_redact=True,
        ),
        "compile": prepare_compile_section_api(raw_compile, apply_redact=True),
        "executions": [
            finalize_execution_for_trace_response(execution_record_to_dict(r), fields=fset, redact=True)
            for r in exec_rows
        ],
        "executions_paging": {
            "limit": cap,
            "offset": off,
            "total": total,
            "next_offset": next_off if has_more else None,
            "has_more": has_more,
        },
        "tactical_incremental_artifacts": prepare_tactical_incremental_section_api(
            ctx.get("_tactical_incremental_artifacts"),
            apply_redact=True,
        ),
    }


@app.get(
    "/v1/orchestrator/tasks/{task_id}/trace/plan",
    dependencies=[Depends(_orch_trace_guard)],
)
async def get_task_trace_plan(
    task_id: str,
    plan_omit_plan_content: bool = False,
    plan_include_chunk_refs: bool = True,
    include_validation_error: bool = True,
) -> dict:
    ctx, source = await _resolve_target_context_for_trace(task_id)
    raw = plan_section_from_context(ctx)
    if not include_validation_error:
        raw = dict(raw)
        raw.pop("plan_list_validation_error", None)
    return {
        "task_id": task_id,
        "context_source": source,
        "plan": prepare_plan_section_api(
            raw,
            omit_plan_content=plan_omit_plan_content,
            include_chunk_refs=plan_include_chunk_refs,
            apply_redact=True,
        ),
    }


@app.get(
    "/v1/orchestrator/tasks/{task_id}/trace/compile",
    dependencies=[Depends(_orch_trace_guard)],
)
async def get_task_trace_compile(task_id: str) -> dict:
    ctx, source = await _resolve_target_context_for_trace(task_id)
    return {
        "task_id": task_id,
        "context_source": source,
        "compile": prepare_compile_section_api(
            compile_section_from_context(ctx),
            apply_redact=True,
        ),
    }


@app.get(
    "/v1/orchestrator/tasks/{task_id}/executions",
    dependencies=[Depends(_orch_trace_guard)],
)
async def list_task_executions(
    task_id: str,
    limit: int = 50,
    offset: int = 0,
    fields: str | None = None,
) -> dict:
    await _ensure_task_exists_for_trace(task_id)
    fset = _parse_execution_fields_or_400(fields)
    cap = clamp_execution_paging_limit(limit)
    off = clamp_execution_paging_offset(offset)
    total = await _TASK_STORE.count_executions_for_task(task_id)
    rows = await _TASK_STORE.list_executions_for_task(task_id, limit=cap, offset=off)
    next_off = off + len(rows)
    has_more = next_off < total
    return {
        "task_id": task_id,
        "limit": cap,
        "offset": off,
        "total": total,
        "next_offset": next_off if has_more else None,
        "has_more": has_more,
        "executions": [
            finalize_execution_for_trace_response(execution_record_to_dict(r), fields=fset, redact=True)
            for r in rows
        ],
    }


@app.get("/v1/orchestrator/mq-status")
async def mq_status() -> dict:
    """
    MQ 模式下列队与消费者概览，便于验证负载均衡与排障。
    返回 **`MQ_TOPIC_AGENT`**（默认 execute_tasks_agent）队列的 messages_ready、messages_unacknowledged、consumers 等；
    非 MQ 模式或无法连接 Management API 时返回 mode 与 error 说明。
    """
    from app.orchestrator_sli_snapshot import fetch_mq_execute_queue_snapshot

    return await fetch_mq_execute_queue_snapshot()


@app.post("/v1/orchestrator/mq-cleanup-zombie-consumers")
async def mq_cleanup_zombie_consumers(
    dry_run: bool = True,
    include_idle: bool = False,
) -> dict:
    """
    清理 **`MQ_TOPIC_AGENT`** 队列疑似僵尸消费者连接。
    - 默认 dry_run=true 只返回候选，不实际关闭连接；
    - include_idle=true 时，也会将 idle 状态连接纳入候选（更激进）。
    """
    mode = (os.getenv("EXECUTION_DISPATCH_MODE") or "http").strip().lower()
    broker_url = (os.getenv("MQ_BROKER_URL") or "").strip()
    topic = (os.getenv("MQ_TOPIC_AGENT") or "execute_tasks_agent").strip() or "execute_tasks_agent"
    if mode != "mq" or not broker_url:
        return {
            "mode": mode,
            "queue": topic,
            "dry_run": dry_run,
            "message": "not in mq mode or MQ_BROKER_URL unset",
            "candidates": [],
            "closed": [],
        }

    u = urlparse(broker_url)
    host = u.hostname or "localhost"
    auth = (u.username or "guest", u.password or "guest")
    queue_url = f"http://{host}:15672/api/queues/%2F/{topic}"
    conns_url = f"http://{host}:15672/api/connections"

    async with httpx.AsyncClient(timeout=8.0) as client:
        q_resp = await client.get(queue_url, auth=auth)
        q_resp.raise_for_status()
        q_data = q_resp.json() or {}

        c_resp = await client.get(conns_url, auth=auth)
        c_resp.raise_for_status()
        conn_list = c_resp.json() or []

        conn_map = {
            str(item.get("name") or ""): item
            for item in conn_list
            if isinstance(item, dict) and str(item.get("name") or "")
        }
        consumer_details = q_data.get("consumer_details") or []
        candidates: list[dict[str, Any]] = []
        for c in consumer_details:
            if not isinstance(c, dict):
                continue
            ch = c.get("channel_details") or {}
            conn_name = str(ch.get("connection_name") or "")
            if not conn_name:
                continue
            conn = conn_map.get(conn_name) or {}
            state = str(conn.get("state") or "")
            channels = int(conn.get("channels") or 0)
            reason = None
            if state and state != "running":
                reason = f"state={state}"
            elif channels <= 0:
                reason = "channels<=0"
            elif include_idle and state == "idle":
                reason = "state=idle"
            if not reason:
                continue
            candidates.append(
                {
                    "connection_name": conn_name,
                    "consumer_tag": c.get("consumer_tag"),
                    "state": state,
                    "channels": channels,
                    "reason": reason,
                }
            )

        closed: list[str] = []
        if not dry_run:
            for item in candidates:
                conn_name = str(item.get("connection_name") or "")
                if not conn_name:
                    continue
                close_url = f"http://{host}:15672/api/connections/{quote(conn_name, safe='')}"
                r = await client.delete(close_url, auth=auth)
                if r.status_code in (204, 200):
                    closed.append(conn_name)

    return {
        "mode": mode,
        "queue": topic,
        "dry_run": dry_run,
        "candidates": candidates,
        "closed": closed,
    }


def _trace_stream_poll_interval() -> float:
    """ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS 默认 1.0 秒；有效范围 [0.1, 60.0]。"""
    try:
        v = float((os.getenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS") or "").strip())
        return max(0.1, min(60.0, v))
    except (ValueError, TypeError):
        return 1.0


def _trace_stream_max_seconds() -> float:
    """ORCH_TRACE_STREAM_MAX_SECONDS 默认 300 秒；有效范围 [5, 3600]。"""
    try:
        v = float((os.getenv("ORCH_TRACE_STREAM_MAX_SECONDS") or "").strip())
        return max(5.0, min(3600.0, v))
    except (ValueError, TypeError):
        return 300.0


@app.get(
    "/v1/orchestrator/tasks/{task_id}/trace/stream",
    dependencies=[Depends(_orch_trace_guard)],
)
async def stream_task_incremental_artifacts(task_id: str):
    """
    P3 SSE：实时推送 INCREMENTAL_ARTIFACT 增量工件流。

    - 仅面Evidence存中活跃任务实时推送新工件（游标跟踪已发送索引）。
    - 任务完成/不在内存：一次性推送全量现存工件后写入 `event: done` 关闭连接。
    - 格式：标准 SSE（`data: <json>\\n\\n`）；心跳使用注释行（`: heartbeat\\n\\n`）。
    - 认证：与 `/trace` 端点一致（`ORCH_TRACE_API_TOKEN` / `X-Orch-Trace-Token`）。
    - ENV：`ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS`（默认 1.0）、`ORCH_TRACE_STREAM_MAX_SECONDS`（默认 300）。
    """
    from fastapi.responses import StreamingResponse as _SR

    # 预检任务存在性（不存在则 404）
    if task_id not in _TASKS:
        rec = await _TASK_STORE.get_task(task_id)
        if not rec:
            raise HTTPException(status_code=404, detail="task not found")

    poll_interval = _trace_stream_poll_interval()
    max_seconds = _trace_stream_max_seconds()

    async def _event_generator():
        deadline = asyncio.get_event_loop().time() + max_seconds
        sent_index = 0  # 已发送的工件条数游标

        while True:
            now = asyncio.get_event_loop().time()
            if now >= deadline:
                yield "event: timeout\ndata: {\"reason\": \"stream_timeout\"}\n\n"
                break

            # 读取当前增量工件列表
            if task_id in _TASKS:
                raw_artifacts = _TASKS[task_id].target_context.get("_tactical_incremental_artifacts")
                is_live = True
            else:
                # 任务已不在内存：尝试从 checkpoint 获取快照
                ck = await load_checkpoint_remote(task_id)
                raw_artifacts = None
                if isinstance(ck, dict):
                    ctx = ck.get("target_context")
                    if isinstance(ctx, dict):
                        raw_artifacts = ctx.get("_tactical_incremental_artifacts")
                is_live = False

            if isinstance(raw_artifacts, list):
                new_items = raw_artifacts[sent_index:]
                if new_items:
                    # prepare_tactical_incremental_section_api 做脱敏 + 字段过滤
                    safe_items = prepare_tactical_incremental_section_api(
                        new_items, apply_redact=True, max_items=len(new_items)
                    )
                    for item in safe_items:
                        data_line = json.dumps(item, ensure_ascii=False)
                        yield f"data: {data_line}\n\n"
                    sent_index += len(new_items)

            if not is_live:
                # 非活跃任务：一次性推送完毕即关闭
                yield "event: done\ndata: {\"reason\": \"task_not_live\"}\n\n"
                break

            # 心跳 + 等待
            yield ": heartbeat\n\n"
            await asyncio.sleep(poll_interval)

    return _SR(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=18081, reload=True)
