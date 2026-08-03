from __future__ import annotations

import importlib
import sys

import pytest

from tests.paths import REPO_ROOT


def _load_gateway_main():
    gateway_root = str(REPO_ROOT / "gateway")
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, gateway_root)
    try:
        return importlib.import_module("app.main")
    finally:
        sys.path.remove(gateway_root)


def test_sync_task_state_does_not_touch_unchanged_terminal_timestamp(monkeypatch):
    gateway = _load_gateway_main()
    writes = []
    monkeypatch.setattr(
        gateway,
        "_get_task_row",
        lambda _task_id: {"status": "DONE", "current_phase": "DONE"},
    )
    monkeypatch.setattr(gateway, "_execute", lambda sql, params=(): writes.append((sql, params)))

    gateway._sync_task_state("task-1", {"status": "DONE", "currentPhase": "DONE"})

    assert writes == []


def test_sync_task_state_updates_timestamp_when_phase_changes(monkeypatch):
    gateway = _load_gateway_main()
    writes = []
    monkeypatch.setattr(
        gateway,
        "_get_task_row",
        lambda _task_id: {"status": "RUNNING", "current_phase": "RECON"},
    )
    monkeypatch.setattr(gateway, "_execute", lambda sql, params=(): writes.append((sql, params)))

    gateway._sync_task_state("task-1", {"status": "RUNNING", "currentPhase": "VULN_SCAN"})

    assert len(writes) == 1
    sql, params = writes[0]
    assert "current_phase = %s" in sql
    assert "updated_at = NOW()" in sql
    assert params == ("VULN_SCAN", "task-1")


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/tasks/task-1/fp-feedback", {"fpId": "fp-1", "humanVerdict": "TRUE_POSITIVE"}),
        ("/api/v1/tasks/task-1/fp-findings/deep-audit", None),
    ],
)
def test_fp_write_routes_reject_missing_login(path, body):
    gateway = _load_gateway_main()
    from fastapi.testclient import TestClient

    response = TestClient(gateway.app).post(path, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/tasks/task-1/fp-feedback", {"fpId": "fp-1", "humanVerdict": "TRUE_POSITIVE"}),
        ("/api/v1/tasks/task-1/fp-findings/deep-audit", None),
    ],
)
def test_fp_write_routes_reject_viewer(path, body):
    gateway = _load_gateway_main()
    from fastapi.testclient import TestClient

    gateway.app.dependency_overrides[gateway.get_current_user] = lambda: gateway.CurrentUser(
        user_id="user-viewer",
        username="viewer",
        role="VIEWER",
        status="ACTIVE",
    )
    try:
        response = TestClient(gateway.app).post(path, json=body)
    finally:
        gateway.app.dependency_overrides.clear()

    assert response.status_code == 403


def test_fp_feedback_rejects_unknown_verdict_before_proxying():
    gateway = _load_gateway_main()
    from fastapi.testclient import TestClient

    gateway.app.dependency_overrides[gateway.get_current_user] = lambda: gateway.CurrentUser(
        user_id="user-operator",
        username="operator",
        role="OPERATOR",
        status="ACTIVE",
    )
    try:
        response = TestClient(gateway.app).post(
            "/api/v1/tasks/task-1/fp-feedback",
            json={"fpId": "fp-1", "humanVerdict": "MAYBE"},
        )
    finally:
        gateway.app.dependency_overrides.clear()

    assert response.status_code == 422
