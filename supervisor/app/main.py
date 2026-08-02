from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from app.domain.models import (
    ActivityStep,
    ConsumeDraftRequest,
    ConsumeDraftResponse,
    CompleteDraftRequest,
    CompleteDraftResponse,
    ConversationMessage,
    ConversationMessageRequest,
    ConversationResponse,
    DraftRequest,
    DraftResponse,
    HealthResponse,
    PentestDraft,
    ProgressSummaryRequest,
    ProgressSummaryResponse,
    WorkflowCapability,
)
from app.clients.llm_client import generate_progress_summary, stream_assistant_response
from app.runtime.graph import run_graph
from app.security.confirmation import create_draft_store_from_env
from app.stores.conversation_store import create_conversation_store_from_env
from app.workflows.registry import workflow_registry

app = FastAPI(title="TrustGuard Supervisor", version="0.1.0")
log = logging.getLogger("trustguard.supervisor")
_conversations = create_conversation_store_from_env()
_drafts = create_draft_store_from_env()


def _actor_id(x_actor_id: str | None) -> str:
    return (x_actor_id or "anonymous").strip() or "anonymous"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    from app.clients.llm_client import llm_enabled

    return HealthResponse(
        status="ok",
        service="supervisor",
        llm_mode="openai-compatible" if llm_enabled() else "deterministic-fallback",
        store_backend=_drafts.backend,
        conversation_store_backend=_conversations.backend,
    )


@app.get("/v1/workflows", response_model=list[WorkflowCapability])
def list_workflows() -> list[WorkflowCapability]:
    return workflow_registry.capabilities()


def _build_draft_response(req: DraftRequest, actor: str) -> DraftResponse:
    conversation_id = req.conversation_id or "conv-" + uuid.uuid4().hex
    conversation = _conversations.append_message(
        conversation_id,
        actor,
        ConversationMessage(id="user-" + uuid.uuid4().hex, role="user", text=req.message.strip()),
    )
    combined = "\n".join(message.text for message in conversation.messages if message.role == "user")
    registered = workflow_registry.resolve(req.workflow_id, combined)
    state = run_graph(combined, registered.adapter.workflow_id)
    draft_model = PentestDraft.model_validate(state.get("draft") or {})
    missing = list(state.get("missing_fields") or [])
    warnings = list(state.get("warnings") or [])
    status = str(state.get("status") or "NEEDS_CLARIFICATION")
    draft_id = None
    token = None
    if not missing:
        record, token = _drafts.put(conversation_id, actor, draft_model)
        draft_id = record.draft_id
    if missing:
        assistant = "我还不能创建任务。请补充：" + "、".join(missing) + "。"
    else:
        assistant = "我已整理好渗透测试任务草稿。确认后我会创建任务并启动现有的 Pentest Workflow。"
    if warnings:
        assistant += " 注意：" + "；".join(warnings)
    return DraftResponse(
        status=status, conversation_id=conversation_id, draft_id=draft_id,
        confirmation_token=token, draft=draft_model, missing_fields=missing,
        warnings=warnings, assistant_message=assistant, activities=state.get("activities") or [],
        workflow_id=registered.adapter.workflow_id,
    )


def _persist_draft_response(response: DraftResponse, actor: str) -> ConversationMessage:
    message = ConversationMessage(
        id="assistant-" + uuid.uuid4().hex,
        role="assistant",
        text=response.assistant_message,
        activities=response.activities,
        draft=response.draft,
        confirmation_token=response.confirmation_token,
    )
    _conversations.append_message(response.conversation_id, actor, message)
    return message


def _sse_event(event: str, data: Any) -> str:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json", by_alias=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


@app.post("/v1/task-agent/draft", response_model=DraftResponse)
def create_draft(req: DraftRequest, x_actor_id: str | None = Header(default=None)) -> DraftResponse:
    actor = _actor_id(x_actor_id)
    response = _build_draft_response(req, actor)
    _persist_draft_response(response, actor)
    return response


@app.post("/v1/task-agent/draft/stream")
async def create_draft_stream(
    req: DraftRequest,
    x_actor_id: str | None = Header(default=None),
) -> StreamingResponse:
    actor = _actor_id(x_actor_id)

    async def events() -> AsyncIterator[str]:
        yield _sse_event(
            "activity",
            ActivityStep(
                id="step-1",
                kind="analysis",
                title="理解任务意图",
                detail="正在提取目标范围、运行时长和风险偏好。",
                status="running",
            ),
        )
        task = asyncio.create_task(asyncio.to_thread(_build_draft_response, req, actor))
        try:
            while not task.done():
                done, _pending = await asyncio.wait({task}, timeout=10.0)
                if not done:
                    yield ": keep-alive\n\n"
            response = await task
        except Exception:
            log.exception("streaming draft generation failed actor_id=%s", actor)
            yield _sse_event("error", {"message": "任务草稿生成失败，请稍后重试。"})
            yield _sse_event("done", {})
            return

        for activity in response.activities:
            yield _sse_event("activity", activity)
        fallback = response.assistant_message
        streamed: list[str] = []
        result_context = response.model_dump(mode="json", by_alias=True)
        chunks: asyncio.Queue[str | None] = asyncio.Queue()

        async def produce_chunks() -> None:
            try:
                async for chunk in stream_assistant_response(result_context, fallback):
                    await chunks.put(chunk)
            finally:
                chunks.put_nowait(None)

        producer = asyncio.create_task(produce_chunks())
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(chunks.get(), timeout=10.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if chunk is None:
                    break
                streamed.append(chunk)
                yield _sse_event("delta", {"text": chunk})
            await producer
        finally:
            if not producer.done():
                producer.cancel()
                with suppress(asyncio.CancelledError):
                    await producer
        response.assistant_message = "".join(streamed) or fallback
        _persist_draft_response(response, actor)
        yield _sse_event("result", response)
        yield _sse_event("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    x_actor_id: str | None = Header(default=None),
) -> ConversationResponse:
    conversation = _conversations.get(conversation_id, _actor_id(x_actor_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    task_id = next((message.task_id for message in reversed(conversation.messages) if message.task_id), None)
    return ConversationResponse(
        conversation_id=conversation.conversation_id,
        messages=conversation.messages,
        task_id=task_id,
    )


@app.post("/v1/conversations/{conversation_id}/messages", response_model=ConversationMessage)
def append_conversation_message(
    conversation_id: str,
    req: ConversationMessageRequest,
    x_actor_id: str | None = Header(default=None),
) -> ConversationMessage:
    message = ConversationMessage.model_validate(req.model_dump())
    conversation = _conversations.append_message(conversation_id, _actor_id(x_actor_id), message)
    return next(item for item in conversation.messages if item.id == message.id)


@app.post("/v1/task-agent/progress-summary", response_model=ProgressSummaryResponse)
async def create_progress_summary(
    req: ProgressSummaryRequest,
    x_actor_id: str | None = Header(default=None),
) -> ProgressSummaryResponse:
    _actor_id(x_actor_id)
    context = req.model_dump(mode="json", by_alias=True, exclude={"fallback_message"})
    message = await generate_progress_summary(context, req.fallback_message)
    return ProgressSummaryResponse(assistant_message=message)


@app.post("/v1/task-agent/drafts/consume", response_model=ConsumeDraftResponse)
def consume_draft(req: ConsumeDraftRequest, x_actor_id: str | None = Header(default=None)) -> ConsumeDraftResponse:
    actor = _actor_id(x_actor_id)
    idem = (req.idempotency_key or "").strip() or hashlib.sha256(req.confirmation_token.encode()).hexdigest()[:32]
    record = _drafts.claim(req.confirmation_token, actor, idem)
    return ConsumeDraftResponse(
        draft_id=record.draft_id,
        conversation_id=record.conversation_id,
        draft=record.draft,
        confirmation_state="COMPLETED" if record.confirmation_state == "COMPLETED" else "CLAIMED",
        task_id=record.task_id or None,
    )


@app.post("/v1/task-agent/drafts/complete", response_model=CompleteDraftResponse)
def complete_draft(req: CompleteDraftRequest, x_actor_id: str | None = Header(default=None)) -> CompleteDraftResponse:
    record = _drafts.complete(
        req.draft_id,
        _actor_id(x_actor_id),
        req.idempotency_key,
        req.task_id,
    )
    return CompleteDraftResponse(draft_id=record.draft_id, task_id=record.task_id)
