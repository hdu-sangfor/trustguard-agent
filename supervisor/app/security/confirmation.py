from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException

from app.domain.models import PentestDraft


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    return (os.getenv("SUPERVISOR_CONFIRMATION_SECRET") or "trustguard-supervisor-dev-secret").encode()


@dataclass
class DraftRecord:
    draft_id: str
    conversation_id: str
    actor_id: str
    draft: PentestDraft
    consumed: bool = False


class DraftStore:
    def __init__(self) -> None:
        self._items: dict[str, DraftRecord] = {}

    def put(self, conversation_id: str, actor_id: str, draft: PentestDraft) -> tuple[DraftRecord, str]:
        draft_id = "draft-" + uuid.uuid4().hex
        record = DraftRecord(draft_id, conversation_id, actor_id, draft)
        self._items[draft_id] = record
        expires = int(time.time()) + 600
        payload = {
            "draft_id": draft_id,
            "conversation_id": conversation_id,
            "actor_id": actor_id,
            "expires": expires,
            "draft_hash": hashlib.sha256(draft.model_dump_json().encode()).hexdigest(),
        }
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        return record, f"{body}.{sig}"

    def consume(self, token: str, actor_id: str) -> DraftRecord:
        try:
            body, signature = token.split(".", 1)
            expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(signature)):
                raise ValueError("signature")
            payload = json.loads(_unb64(body).decode())
            if int(payload.get("expires") or 0) < int(time.time()):
                raise ValueError("expired")
            if str(payload.get("actor_id") or "") != actor_id:
                raise ValueError("actor")
            record = self._items.get(str(payload.get("draft_id") or ""))
            if record is None or record.consumed:
                raise ValueError("consumed")
            if hashlib.sha256(record.draft.model_dump_json().encode()).hexdigest() != payload.get("draft_hash"):
                raise ValueError("tampered")
            record.consumed = True
            return record
        except Exception as exc:
            raise HTTPException(status_code=400, detail="草稿确认令牌无效、已过期或已使用") from exc
