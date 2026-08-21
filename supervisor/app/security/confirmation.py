from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Protocol

from fastapi import HTTPException

from app.domain.models import AlertTriageDraft, PentestDraft, WorkflowDraft


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    return (os.getenv("SUPERVISOR_CONFIRMATION_SECRET") or "trustguard-supervisor-dev-secret").encode()


def _draft_hash(draft: WorkflowDraft) -> str:
    return hashlib.sha256(draft.model_dump_json().encode()).hexdigest()


@dataclass
class DraftRecord:
    draft_id: str
    conversation_id: str
    actor_id: str
    draft: WorkflowDraft
    confirmation_state: str = "AVAILABLE"
    claim_key: str = ""
    claimed_until: int = 0
    task_id: str = ""


class DraftStoreProtocol(Protocol):
    backend: str

    def put(self, conversation_id: str, actor_id: str, draft: WorkflowDraft) -> tuple[DraftRecord, str]: ...
    def claim(self, token: str, actor_id: str, idempotency_key: str) -> DraftRecord: ...
    def complete(self, draft_id: str, actor_id: str, idempotency_key: str, task_id: str) -> DraftRecord: ...


class _TokenMixin:
    token_ttl_seconds = 600

    def _issue_token(self, record: DraftRecord) -> str:
        payload = {
            "draft_id": record.draft_id,
            "conversation_id": record.conversation_id,
            "actor_id": record.actor_id,
            "expires": int(time.time()) + self.token_ttl_seconds,
            "draft_hash": _draft_hash(record.draft),
        }
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def _verify_token(self, token: str, actor_id: str) -> dict[str, Any]:
        try:
            body, signature = token.split(".", 1)
            expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(expected, signature):
                raise ValueError("signature")
            payload = json.loads(_unb64(body).decode())
            if int(payload.get("expires") or 0) < int(time.time()):
                raise ValueError("expired")
            if str(payload.get("actor_id") or "") != actor_id:
                raise ValueError("actor")
            return payload
        except Exception as exc:
            raise HTTPException(status_code=400, detail="草稿确认令牌无效或已过期") from exc

    @staticmethod
    def _validate_record(record: DraftRecord | None, payload: dict[str, Any]) -> DraftRecord:
        if record is None or _draft_hash(record.draft) != payload.get("draft_hash"):
            raise HTTPException(status_code=400, detail="草稿不存在或内容校验失败")
        return record


class DraftStore(_TokenMixin):
    """Thread-safe in-memory backend used by local runs and unit tests."""

    backend = "memory"

    def __init__(self) -> None:
        self._items: dict[str, DraftRecord] = {}
        self._lock = threading.RLock()

    def put(self, conversation_id: str, actor_id: str, draft: WorkflowDraft) -> tuple[DraftRecord, str]:
        record = DraftRecord("draft-" + uuid.uuid4().hex, conversation_id, actor_id, draft)
        with self._lock:
            self._items[record.draft_id] = record
        return record, self._issue_token(record)

    def claim(self, token: str, actor_id: str, idempotency_key: str) -> DraftRecord:
        payload = self._verify_token(token, actor_id)
        now = int(time.time())
        with self._lock:
            record = self._validate_record(self._items.get(str(payload.get("draft_id") or "")), payload)
            if record.confirmation_state == "COMPLETED":
                if record.claim_key != idempotency_key:
                    raise HTTPException(status_code=409, detail="草稿已由其他确认请求完成")
                return replace(record)
            if (
                record.confirmation_state == "CLAIMED"
                and record.claim_key != idempotency_key
                and record.claimed_until > now
            ):
                raise HTTPException(status_code=409, detail="草稿正在由其他确认请求处理")
            record.confirmation_state = "CLAIMED"
            record.claim_key = idempotency_key
            record.claimed_until = now + 120
            return replace(record)

    def complete(self, draft_id: str, actor_id: str, idempotency_key: str, task_id: str) -> DraftRecord:
        with self._lock:
            record = self._items.get(draft_id)
            if record is None or record.actor_id != actor_id:
                raise HTTPException(status_code=404, detail="草稿不存在")
            if record.claim_key != idempotency_key:
                raise HTTPException(status_code=409, detail="确认请求与草稿 claim 不匹配")
            if record.confirmation_state == "COMPLETED":
                if record.task_id != task_id:
                    raise HTTPException(status_code=409, detail="草稿已绑定其他任务")
                return replace(record)
            record.confirmation_state = "COMPLETED"
            record.task_id = task_id
            record.claimed_until = 0
            return replace(record)

    def consume(self, token: str, actor_id: str) -> DraftRecord:
        """Legacy one-shot behavior retained for old callers and compatibility tests."""
        return self.claim(token, actor_id, "legacy-" + uuid.uuid4().hex)


class RedisDraftStore(_TokenMixin):
    backend = "redis"

    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _key(draft_id: str) -> str:
        return f"supervisor:draft:{draft_id}"

    @staticmethod
    def _from_hash(draft_id: str, data: dict[str, str]) -> DraftRecord | None:
        if not data:
            return None
        raw_draft = data.get("draft_json") or "{}"
        try:
            draft_data = json.loads(raw_draft)
        except json.JSONDecodeError:
            draft_data = {}
        draft: WorkflowDraft
        if draft_data.get("workflow_id") == "alert_triage" or "alert_uuid" in draft_data:
            draft = AlertTriageDraft.model_validate(draft_data)
        else:
            draft = PentestDraft.model_validate(draft_data)
        return DraftRecord(
            draft_id=draft_id,
            conversation_id=data.get("conversation_id") or "",
            actor_id=data.get("actor_id") or "",
            draft=draft,
            confirmation_state=data.get("confirmation_state") or "AVAILABLE",
            claim_key=data.get("claim_key") or "",
            claimed_until=int(data.get("claimed_until") or 0),
            task_id=data.get("task_id") or "",
        )

    def put(self, conversation_id: str, actor_id: str, draft: WorkflowDraft) -> tuple[DraftRecord, str]:
        record = DraftRecord("draft-" + uuid.uuid4().hex, conversation_id, actor_id, draft)
        key = self._key(record.draft_id)
        self._client.hset(key, mapping={
            "conversation_id": conversation_id,
            "actor_id": actor_id,
            "draft_json": draft.model_dump_json(),
            "confirmation_state": "AVAILABLE",
            "claim_key": "",
            "claimed_until": "0",
            "task_id": "",
        })
        self._client.expire(key, 86400)
        return record, self._issue_token(record)

    def claim(self, token: str, actor_id: str, idempotency_key: str) -> DraftRecord:
        from redis.exceptions import WatchError

        payload = self._verify_token(token, actor_id)
        draft_id = str(payload.get("draft_id") or "")
        key = self._key(draft_id)
        for _attempt in range(5):
            try:
                with self._client.pipeline() as pipe:
                    pipe.watch(key)
                    record = self._validate_record(self._from_hash(draft_id, pipe.hgetall(key)), payload)
                    now = int(time.time())
                    if record.confirmation_state == "COMPLETED":
                        if record.claim_key != idempotency_key:
                            raise HTTPException(status_code=409, detail="草稿已由其他确认请求完成")
                        return record
                    if (
                        record.confirmation_state == "CLAIMED"
                        and record.claim_key != idempotency_key
                        and record.claimed_until > now
                    ):
                        raise HTTPException(status_code=409, detail="草稿正在由其他确认请求处理")
                    pipe.multi()
                    pipe.hset(key, mapping={
                        "confirmation_state": "CLAIMED",
                        "claim_key": idempotency_key,
                        "claimed_until": str(now + 120),
                    })
                    pipe.expire(key, 86400)
                    pipe.execute()
                    return replace(
                        record,
                        confirmation_state="CLAIMED",
                        claim_key=idempotency_key,
                        claimed_until=now + 120,
                    )
            except WatchError:
                continue
        raise HTTPException(status_code=409, detail="草稿确认并发冲突，请重试")

    def complete(self, draft_id: str, actor_id: str, idempotency_key: str, task_id: str) -> DraftRecord:
        from redis.exceptions import WatchError

        key = self._key(draft_id)
        for _attempt in range(5):
            try:
                with self._client.pipeline() as pipe:
                    pipe.watch(key)
                    record = self._from_hash(draft_id, pipe.hgetall(key))
                    if record is None or record.actor_id != actor_id:
                        raise HTTPException(status_code=404, detail="草稿不存在")
                    if record.claim_key != idempotency_key:
                        raise HTTPException(status_code=409, detail="确认请求与草稿 claim 不匹配")
                    if record.confirmation_state == "COMPLETED":
                        if record.task_id != task_id:
                            raise HTTPException(status_code=409, detail="草稿已绑定其他任务")
                        return record
                    pipe.multi()
                    pipe.hset(key, mapping={
                        "confirmation_state": "COMPLETED",
                        "task_id": task_id,
                        "claimed_until": "0",
                    })
                    pipe.expire(key, 86400)
                    pipe.execute()
                    return replace(record, confirmation_state="COMPLETED", task_id=task_id, claimed_until=0)
            except WatchError:
                continue
        raise HTTPException(status_code=409, detail="草稿完成状态并发冲突，请重试")


def create_draft_store_from_env() -> DraftStoreProtocol:
    backend = (os.getenv("SUPERVISOR_STORE_BACKEND") or "memory").strip().lower()
    if backend != "redis":
        return DraftStore()
    try:
        import redis

        client = redis.Redis.from_url(
            os.getenv("REDIS_URL") or "redis://localhost:6379/0",
            decode_responses=True,
        )
        client.ping()
        return RedisDraftStore(client)
    except Exception:
        if (os.getenv("DEPLOYMENT_MODE") or "").strip().lower() == "docker":
            raise
        return DraftStore()
