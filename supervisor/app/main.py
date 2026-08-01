from __future__ import annotations

import uuid

from fastapi import FastAPI, Header, HTTPException

from app.domain.models import (
    ConsumeDraftRequest,
    ConsumeDraftResponse,
    DraftRequest,
    DraftResponse,
    HealthResponse,
    PentestDraft,
)
from app.runtime.graph import run_graph
from app.security.confirmation import DraftStore
from app.stores.conversation_store import InMemoryConversationStore

app = FastAPI(title="TrustGuard Supervisor", version="0.1.0")
_conversations = InMemoryConversationStore()
_drafts = DraftStore()


def _actor_id(x_actor_id: str | None) -> str:
    return (x_actor_id or "anonymous").strip() or "anonymous"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    from app.clients.llm_client import llm_enabled

    return HealthResponse(status="ok", service="supervisor", llm_mode="openai-compatible" if llm_enabled() else "deterministic-fallback")


@app.post("/v1/task-agent/draft", response_model=DraftResponse)
def create_draft(req: DraftRequest, x_actor_id: str | None = Header(default=None)) -> DraftResponse:
    actor = _actor_id(x_actor_id)
    conversation_id = req.conversation_id or "conv-" + uuid.uuid4().hex
    conversation = _conversations.append(conversation_id, actor, req.message)
    combined = "\n".join(conversation.messages)
    state = run_graph(combined)
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
    )


@app.post("/v1/task-agent/drafts/consume", response_model=ConsumeDraftResponse)
def consume_draft(req: ConsumeDraftRequest, x_actor_id: str | None = Header(default=None)) -> ConsumeDraftResponse:
    record = _drafts.consume(req.confirmation_token, _actor_id(x_actor_id))
    return ConsumeDraftResponse(draft_id=record.draft_id, conversation_id=record.conversation_id, draft=record.draft)
