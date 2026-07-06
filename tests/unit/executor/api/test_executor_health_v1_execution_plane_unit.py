"""Executor GET /health 内嵌 v1_execution_plane 只读摘要。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

# 与 `app.main.V1_EXECUTION_PLANE_SCHEMA_VERSION` 对齐的契约键全集。
_V1_EXECUTION_PLANE_SCHEMA7_KEYS = frozenset(
    {
        "schema_version",
        "execution_dispatch_mode",
        "mq_fast_lane_module",
        "mq_agent_lane_module",
        "mq_topic_execute",
        "mq_topic_agent",
        "mq_broker_configured",
        "mq_dispatch_ready",
        "agent_lane_mq_enabled",
        "agent_lane_allowlist_count",
        "agent_lane_routing_active",
        "agent_lane_publish_ready",
        "dev_cli_hints",
        "agent_stdout_summary_default_max_chars",
        "skill_containers_enabled",
        "require_execution_kind",
    }
)


def _agent_stdout_budget_env_key() -> str:
    from app.micro_executor.outputs import MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV

    return MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV


def test_executor_health_includes_v1_execution_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    from app.main import V1_EXECUTION_PLANE_SCHEMA_VERSION, app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    v1 = body.get("v1_execution_plane")
    assert isinstance(v1, dict)
    assert v1.get("schema_version") == V1_EXECUTION_PLANE_SCHEMA_VERSION
    assert v1.get("execution_dispatch_mode") == "http"
    assert v1.get("mq_fast_lane_module") == "app.mq_execute_consumer"
    assert v1.get("mq_agent_lane_module") == "app.mq_agent_daemon"
    assert v1.get("mq_topic_execute") == "execute_tasks_agent"
    assert v1.get("mq_topic_agent") == "execute_tasks_agent"
    assert v1.get("mq_broker_configured") is False
    assert v1.get("mq_dispatch_ready") is False
    assert v1.get("agent_lane_mq_enabled") is False
    assert v1.get("agent_lane_allowlist_count") == 0
    assert v1.get("agent_lane_routing_active") is False
    assert v1.get("agent_lane_publish_ready") is False
    hints = v1.get("dev_cli_hints")
    assert isinstance(hints, list) and "dev/mq/mq_worker.py" in hints and "dev/mq/mq_agent_daemon.py" in hints
    assert isinstance(v1.get("agent_stdout_summary_default_max_chars"), int)
    assert v1["agent_stdout_summary_default_max_chars"] >= 256
    assert isinstance(v1.get("skill_containers_enabled"), bool)
    dumped = json.dumps(body, ensure_ascii=False)
    assert "MQ_BROKER_URL" not in dumped
    assert "amqp://" not in dumped.lower()


def test_executor_health_v1_dispatch_mode_invalid_falls_back_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "bogus")
    from app.main import app

    v1 = TestClient(app).get("/health").json().get("v1_execution_plane") or {}
    assert v1.get("execution_dispatch_mode") == "http"
    assert v1.get("mq_dispatch_ready") is False


def test_executor_health_v1_mq_publish_ready_matches_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "mq")
    monkeypatch.setenv("MQ_BROKER_URL", "amqp://u:s@h:1/")
    monkeypatch.delenv("V1_AGENT_LANE_MQ_ENABLED", raising=False)
    monkeypatch.delenv("MQ_AGENT_LANE_SKILL_IDS", raising=False)
    from app.main import app

    client = TestClient(app)
    body = client.get("/health").json()
    v1 = body.get("v1_execution_plane") or {}
    assert v1.get("execution_dispatch_mode") == "mq"
    assert v1.get("mq_broker_configured") is True
    assert v1.get("mq_dispatch_ready") is True
    assert v1.get("agent_lane_routing_active") is True
    assert v1.get("agent_lane_publish_ready") is True
    dumped = json.dumps(body, ensure_ascii=False)
    assert "amqp://" not in dumped.lower()


def test_executor_health_v1_mq_topics_reflect_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MQ_TOPIC_AGENT", "agent-lane-topic")
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    from app.main import app

    client = TestClient(app)
    r = client.get("/health")
    v1 = r.json().get("v1_execution_plane") or {}
    assert v1.get("mq_topic_execute") == "agent-lane-topic"
    assert v1.get("mq_topic_agent") == "agent-lane-topic"


def test_executor_health_v1_execution_plane_schema7_exact_keyset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.delenv("EXECUTION_DISPATCH_MODE", raising=False)
    monkeypatch.delenv("V1_AGENT_LANE_MQ_ENABLED", raising=False)
    monkeypatch.delenv("MQ_AGENT_LANE_SKILL_IDS", raising=False)
    monkeypatch.delenv("MQ_TOPIC_AGENT", raising=False)
    from app.main import V1_EXECUTION_PLANE_SCHEMA_VERSION, app

    v1 = TestClient(app).get("/health").json().get("v1_execution_plane") or {}
    assert v1.get("schema_version") == V1_EXECUTION_PLANE_SCHEMA_VERSION
    assert frozenset(v1.keys()) == _V1_EXECUTION_PLANE_SCHEMA7_KEYS


def test_executor_health_mq_broker_configured_true_without_leaking_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MQ_BROKER_URL", "amqp://user:secret@host:5672/vhost")
    from app.main import app

    client = TestClient(app)
    r = client.get("/health")
    body = r.json()
    v1 = body.get("v1_execution_plane") or {}
    assert v1.get("mq_broker_configured") is True
    dumped = json.dumps(body, ensure_ascii=False)
    assert "secret" not in dumped
    assert "amqp://" not in dumped.lower()


def test_executor_health_v1_agent_stdout_max_chars_reflects_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.setenv(_agent_stdout_budget_env_key(), "1800")
    from app.main import app

    v1 = TestClient(app).get("/health").json().get("v1_execution_plane") or {}
    assert v1.get("agent_stdout_summary_default_max_chars") == 1800


def test_executor_health_v1_agent_stdout_invalid_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.setenv(_agent_stdout_budget_env_key(), "bogus")
    from app.main import app

    v1 = TestClient(app).get("/health").json().get("v1_execution_plane") or {}
    assert v1.get("agent_stdout_summary_default_max_chars") == 500 * 4


def test_executor_health_v1_agent_stdout_env_int_trimmed_of_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.setenv(_agent_stdout_budget_env_key(), "  1900\t")
    from app.main import app

    v1 = TestClient(app).get("/health").json().get("v1_execution_plane") or {}
    assert v1.get("agent_stdout_summary_default_max_chars") == 1900
