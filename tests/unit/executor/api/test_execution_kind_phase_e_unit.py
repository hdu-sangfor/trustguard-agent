"""execution_kind guard: only native skill execution is supported."""
from __future__ import annotations

import sys
from contextlib import contextmanager

import pytest

from tests.paths import REPO_ROOT

_EX = str(REPO_ROOT / "executor")


@contextmanager
def _executor_env():
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, _EX)
    try:
        yield
    finally:
        if _EX in sys.path:
            sys.path.remove(_EX)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


class TestExecutionKindExplicit:
    def test_explicit_skill(self):
        with _executor_env():
            from app.models import SkillRequest

            r = SkillRequest(
                task_id="t",
                skill_id="nmap",
                target="http://a",
                params={},
                allowed_target="http://a",
                execution_kind="skill",
            )
            assert r.execution_kind == "skill"
            assert r.execution_kind_explicit is True

    def test_derived_skill_from_nmap(self):
        with _executor_env():
            from app.models import SkillRequest

            r = SkillRequest(
                task_id="t",
                skill_id="nmap",
                target="http://a",
                params={},
                allowed_target="http://a",
            )
            assert r.execution_kind == "skill"
            assert r.execution_kind_explicit is False

    def test_agent_kind_rejected(self):
        with _executor_env():
            from app.models import SkillRequest
            from pydantic import ValidationError

            with pytest.raises(ValidationError):
                SkillRequest(
                    task_id="t",
                    skill_id="nmap",
                    target="http://a",
                    params={},
                    allowed_target="http://a",
                    execution_kind="agent",
                )

    def test_client_cannot_forge_explicit_flag(self):
        with _executor_env():
            from app.models import SkillRequest

            r = SkillRequest(
                task_id="t",
                skill_id="nmap",
                target="http://a",
                params={},
                allowed_target="http://a",
                execution_kind_explicit=True,
            )
            assert r.execution_kind == "skill"
            assert r.execution_kind_explicit is False


class TestRequireExecutionKindGuard:
    def test_guard_off_allows_derived(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("EXECUTOR_REQUIRE_EXECUTION_KIND", raising=False)
        with _executor_env():
            from app.main import _require_execution_kind

            assert _require_execution_kind() is False

    def test_guard_on_values(self, monkeypatch: pytest.MonkeyPatch):
        with _executor_env():
            from app.main import _require_execution_kind

            for val in ("1", "true", "yes", "on", "TRUE", "  1  "):
                monkeypatch.setenv("EXECUTOR_REQUIRE_EXECUTION_KIND", val)
                assert _require_execution_kind() is True

    def test_guard_off_values(self, monkeypatch: pytest.MonkeyPatch):
        with _executor_env():
            from app.main import _require_execution_kind

            for val in ("0", "false", "no", "off", "", "bogus"):
                monkeypatch.setenv("EXECUTOR_REQUIRE_EXECUTION_KIND", val)
                assert _require_execution_kind() is False

    def test_http_execute_rejects_when_guard_on_and_no_explicit_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("EXECUTOR_REQUIRE_EXECUTION_KIND", "1")
        with _executor_env():
            from fastapi.testclient import TestClient
            from app.main import app

            client = TestClient(app, raise_server_exceptions=False)
            r = client.post(
                "/v1/execute",
                json={
                    "task_id": "t",
                    "skill_id": "nmap",
                    "target": "http://a",
                    "params": {},
                    "allowed_target": "http://a",
                },
            )
            assert r.status_code == 400
            assert "execution_kind is required" in r.json().get("detail", "")

    def test_http_execute_allows_when_guard_on_and_explicit_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("EXECUTOR_REQUIRE_EXECUTION_KIND", "1")
        with _executor_env():
            from fastapi.testclient import TestClient
            from app.main import app

            client = TestClient(app, raise_server_exceptions=False)
            r = client.post(
                "/v1/execute",
                json={
                    "task_id": "t",
                    "skill_id": "nmap",
                    "target": "http://a",
                    "params": {},
                    "allowed_target": "http://a",
                    "execution_kind": "skill",
                },
            )
            detail = r.json().get("detail", "")
            assert "execution_kind is required" not in detail

    def test_http_execute_allows_when_guard_off(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("EXECUTOR_REQUIRE_EXECUTION_KIND", raising=False)
        with _executor_env():
            from fastapi.testclient import TestClient
            from app.main import app

            client = TestClient(app, raise_server_exceptions=False)
            r = client.post(
                "/v1/execute",
                json={
                    "task_id": "t",
                    "skill_id": "nmap",
                    "target": "http://a",
                    "params": {},
                    "allowed_target": "http://a",
                },
            )
            detail = r.json().get("detail", "")
            assert "execution_kind is required" not in detail


class TestExecuteAgentEndpointRemoved:
    def test_execute_agent_endpoint_removed(self):
        with _executor_env():
            from fastapi.testclient import TestClient
            from app.main import app

            client = TestClient(app, raise_server_exceptions=False)
            r = client.post(
                "/v1/execute_agent",
                json={
                    "task_id": "t",
                    "target": "http://a",
                    "params": {},
                    "allowed_target": "http://a",
                },
            )
            assert r.status_code == 404


class TestHealthRequireExecutionKind:
    def test_health_includes_require_execution_kind_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("EXECUTOR_REQUIRE_EXECUTION_KIND", raising=False)
        monkeypatch.delenv("MQ_BROKER_URL", raising=False)
        with _executor_env():
            from fastapi.testclient import TestClient
            from app.main import app

            v1 = TestClient(app).get("/health").json().get("v1_execution_plane") or {}
            assert v1.get("require_execution_kind") is False

    def test_health_includes_require_execution_kind_true(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EXECUTOR_REQUIRE_EXECUTION_KIND", "1")
        monkeypatch.delenv("MQ_BROKER_URL", raising=False)
        with _executor_env():
            from fastapi.testclient import TestClient
            from app.main import app

            v1 = TestClient(app).get("/health").json().get("v1_execution_plane") or {}
            assert v1.get("require_execution_kind") is True
