from __future__ import annotations

import importlib
import sys

import pytest
from pydantic import ValidationError

from tests.paths import REPO_ROOT


def _load_main_module():
    orchestrator_root = str(REPO_ROOT / "orchestrator")
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orchestrator_root)
    try:
        main = importlib.import_module("app.main")
        models = importlib.import_module("app.models")
        persistence = importlib.import_module("app.core.fp_persistence")
        tracker = importlib.import_module("app.core.fp_tracker")
        return main, models, persistence, tracker
    finally:
        sys.path.remove(orchestrator_root)


def test_fp_feedback_rejects_unknown_verdict():
    _main, models, _persistence, _tracker = _load_main_module()

    with pytest.raises(ValidationError):
        models.FPFeedbackRequest(fpId="fp-1", humanVerdict="MAYBE")


@pytest.mark.asyncio
async def test_fp_feedback_updates_record_after_task_state_is_gone(monkeypatch):
    main, models, persistence, tracker = _load_main_module()
    task_id = "task-fp-history"
    main._TASKS.pop(task_id, None)
    stored = {
        "fp_id": "fp-history-1",
        "task_id": task_id,
        "finding_signature": "signature",
        "template_id": "nuclei/test",
        "url": "https://example.test/",
        "title": "Example finding",
        "severity": "medium",
        "source_skill_id": "nuclei",
        "source_phase": "VULN_SCAN",
        "current_verdict": "UNVERIFIED",
        "verification_source": "",
        "verification_reasoning": "",
        "detected_at": "2026-08-03T00:00:00Z",
        "verified_at": None,
    }
    persisted = []
    traces = []

    monkeypatch.setattr(persistence, "load_fp_records", lambda requested: [stored] if requested == task_id else [])

    def fake_upsert(record, *, raise_on_error=False):
        persisted.append((dict(record), raise_on_error))
        return True

    async def fake_kb(_record):
        return None

    async def fake_trace(event):
        traces.append(event)

    monkeypatch.setattr(persistence, "upsert_fp_record", fake_upsert)
    monkeypatch.setattr(tracker, "upsert_to_fp_kb", fake_kb)
    monkeypatch.setattr(main, "_emit_trace", fake_trace)

    result = await main.submit_fp_feedback(
        task_id,
        models.FPFeedbackRequest(
            fpId="fp-history-1",
            humanVerdict="FALSE_POSITIVE",
            feedback="人工复核未发现漏洞",
        ),
    )

    assert result == {"fpId": "fp-history-1", "accepted": True, "verdict": "FALSE_POSITIVE"}
    assert persisted[0][1] is True
    assert persisted[0][0]["current_verdict"] == "FALSE_POSITIVE"
    assert persisted[0][0]["verification_source"] == "HUMAN"
    assert traces[-1].event_type == "FP_HUMAN_FEEDBACK"


@pytest.mark.asyncio
async def test_fp_feedback_does_not_accept_when_durable_write_fails(monkeypatch):
    main, models, persistence, tracker = _load_main_module()
    task_id = "task-fp-write-failure"
    main._TASKS.pop(task_id, None)
    stored = {
        "fp_id": "fp-write-failure-1",
        "task_id": task_id,
        "current_verdict": "UNVERIFIED",
        "verification_source": "",
    }

    monkeypatch.setattr(persistence, "load_fp_records", lambda _task_id: [stored])

    def fail_upsert(_record, *, raise_on_error=False):
        assert raise_on_error is True
        raise RuntimeError("database unavailable")

    async def fail_if_called(_record):
        raise AssertionError("KB update must not run after the durable write fails")

    monkeypatch.setattr(persistence, "upsert_fp_record", fail_upsert)
    monkeypatch.setattr(tracker, "upsert_to_fp_kb", fail_if_called)

    with pytest.raises(main.HTTPException) as exc_info:
        await main.submit_fp_feedback(
            task_id,
            models.FPFeedbackRequest(
                fpId="fp-write-failure-1",
                humanVerdict="TRUE_POSITIVE",
            ),
        )

    assert exc_info.value.status_code == 503
    assert stored["current_verdict"] == "UNVERIFIED"
    assert stored["verification_source"] == ""
