from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domain.models import PentestDraft
from app.security.confirmation import DraftStore


def _draft() -> PentestDraft:
    return PentestDraft(name="demo", target="https://test.example.com")


def test_confirmation_token_is_actor_bound_and_one_time(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_CONFIRMATION_SECRET", "unit-test-secret")
    store = DraftStore()
    record, token = store.put("conv-1", "actor-1", _draft())

    with pytest.raises(HTTPException):
        store.consume(token, "actor-2")

    consumed = store.consume(token, "actor-1")
    assert consumed.draft_id == record.draft_id

    with pytest.raises(HTTPException):
        store.consume(token, "actor-1")


def test_confirmation_token_rejects_tampering(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_CONFIRMATION_SECRET", "unit-test-secret")
    store = DraftStore()
    _record, token = store.put("conv-1", "actor-1", _draft())
    body, signature = token.split(".", 1)
    tampered = f"{body}.{signature[:-1]}x"

    with pytest.raises(HTTPException):
        store.consume(tampered, "actor-1")


def test_confirmation_claim_is_retryable_and_completion_is_replayable(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_CONFIRMATION_SECRET", "unit-test-secret")
    store = DraftStore()
    record, token = store.put("conv-1", "actor-1", _draft())

    first = store.claim(token, "actor-1", "idem-1")
    retry = store.claim(token, "actor-1", "idem-1")
    completed = store.complete(record.draft_id, "actor-1", "idem-1", "task-1")
    completed_retry = store.complete(record.draft_id, "actor-1", "idem-1", "task-1")
    replay = store.claim(token, "actor-1", "idem-1")

    assert first.confirmation_state == retry.confirmation_state == "CLAIMED"
    assert completed.confirmation_state == "COMPLETED"
    assert completed_retry.task_id == "task-1"
    assert replay.task_id == "task-1"
    with pytest.raises(HTTPException) as complete_exc:
        store.complete(record.draft_id, "actor-1", "idem-1", "task-other")
    assert complete_exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        store.claim(token, "actor-1", "idem-other")
    assert exc.value.status_code == 409
