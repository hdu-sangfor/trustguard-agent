"""O-05：`v1_mq_lanes` 与执行器 `v1_execution_plane` 的契约键对齐（子集 + 同源 ENV 下取值一致）。"""
from __future__ import annotations

import pytest

from tests.executor_test_env import executor_sys_path_isolated
from tests.orchestrator_test_env import orchestrator_sys_path_isolated
from tests.unit.executor.api.test_executor_health_v1_execution_plane_unit import (
    _V1_EXECUTION_PLANE_SCHEMA7_KEYS,
)


def test_v1_mq_lane_health_keys_are_subset_of_execution_plane_schema() -> None:
    """编排器健康摘要中的 MQ 车道键须均可在执行器 `v1_execution_plane` 中找到，避免双端运维权漂移。"""
    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch_keys = frozenset(v1_mq_lane_health_summary().keys())
    assert orch_keys <= _V1_EXECUTION_PLANE_SCHEMA7_KEYS
    assert orch_keys  # 编排器侧非空


def test_v1_mq_lane_and_execution_plane_values_match_mq_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """同源 ENV 下，重叠键取值须与编排器 `v1_mq_lane_health_summary` 一致（防双实现漂移）。"""
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "mq")
    monkeypatch.setenv("MQ_BROKER_URL", "amqp://user:pass@broker:5672/vh")
    monkeypatch.setenv("MQ_TOPIC_AGENT", "lane-agent")
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    with executor_sys_path_isolated():
        from app.main import _v1_execution_plane_summary

        exe = _v1_execution_plane_summary()

    for k, v in orch.items():
        assert exe.get(k) == v, f"O-05 parity mismatch {k!r}: executor={exe.get(k)!r} orchestrator={v!r}"
    assert orch["mq_topic_execute"] == "lane-agent"
    assert orch["mq_topic_agent"] == "lane-agent"


def test_v1_mq_lane_and_execution_plane_values_match_default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.delenv("EXECUTION_DISPATCH_MODE", raising=False)
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)
    monkeypatch.delenv("MQ_TOPIC_AGENT", raising=False)

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    with executor_sys_path_isolated():
        from app.main import _v1_execution_plane_summary

        exe = _v1_execution_plane_summary()

    for k, v in orch.items():
        assert exe.get(k) == v, k


def test_v1_mq_lane_single_queue_routing_follows_mq_dispatch_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """单队列模式：`agent_lane_*` 与 `mq_dispatch_ready` 一致（不再依赖 allowlist）。"""
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.delenv("EXECUTION_DISPATCH_MODE", raising=False)

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    assert orch["agent_lane_allowlist_count"] == 0
    assert orch["agent_lane_routing_active"] is False
    assert orch["agent_lane_publish_ready"] is False
    assert orch["agent_lane_mq_enabled"] is False


def test_v1_mq_lane_and_execution_plane_values_match_invalid_dispatch_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非法 `EXECUTION_DISPATCH_MODE` 双端均回落 `http`，即使有 broker 也不应 `mq_dispatch_ready`。"""
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "grpc")
    monkeypatch.setenv("MQ_BROKER_URL", "amqp://user:pass@broker:5672/vh")
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)
    monkeypatch.delenv("MQ_TOPIC_AGENT", raising=False)

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    with executor_sys_path_isolated():
        from app.main import _v1_execution_plane_summary

        exe = _v1_execution_plane_summary()

    for k, v in orch.items():
        assert exe.get(k) == v, k
    assert orch["execution_dispatch_mode"] == "http"
    assert orch["mq_broker_configured"] is True
    assert orch["mq_dispatch_ready"] is False


def test_v1_mq_lane_and_execution_plane_values_match_dispatch_mode_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EXECUTION_DISPATCH_MODE` 大小写不敏感：`MQ` 与 `mq` 等价。"""
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "MQ")
    monkeypatch.setenv("MQ_BROKER_URL", "amqp://user:pass@broker:5672/vh")
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)
    monkeypatch.delenv("MQ_TOPIC_AGENT", raising=False)

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    with executor_sys_path_isolated():
        from app.main import _v1_execution_plane_summary

        exe = _v1_execution_plane_summary()

    for k, v in orch.items():
        assert exe.get(k) == v, k
    assert orch["execution_dispatch_mode"] == "mq"
    assert orch["mq_dispatch_ready"] is True
    assert orch["agent_lane_publish_ready"] is True


def test_v1_mq_lane_and_execution_plane_whitespace_only_broker_url_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MQ_BROKER_URL` 仅空白（strip 后空）时双端均视为 broker 未配置，`mq_dispatch_ready` 为假。"""
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "mq")
    monkeypatch.setenv("MQ_BROKER_URL", "  \t  ")
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)
    monkeypatch.delenv("MQ_TOPIC_AGENT", raising=False)

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    with executor_sys_path_isolated():
        from app.main import _v1_execution_plane_summary

        exe = _v1_execution_plane_summary()

    for k, v in orch.items():
        assert exe.get(k) == v, k
    assert orch["mq_broker_configured"] is False
    assert orch["mq_dispatch_ready"] is False


def test_v1_mq_lane_and_execution_plane_topic_agent_trim_and_empty_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MQ_TOPIC_AGENT` 首尾空白去除；strip 后空串回落默认队列名。`mq_topic_execute` 与之相同。"""
    monkeypatch.delenv("MQ_BROKER_URL", raising=False)
    monkeypatch.delenv("EXECUTION_DISPATCH_MODE", raising=False)
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)
    monkeypatch.setenv("MQ_TOPIC_AGENT", "  \t ")

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    with executor_sys_path_isolated():
        from app.main import _v1_execution_plane_summary

        exe = _v1_execution_plane_summary()

    for k, v in orch.items():
        assert exe.get(k) == v, k
    assert orch["mq_topic_execute"] == "execute_tasks_agent"
    assert orch["mq_topic_agent"] == "execute_tasks_agent"


def test_v1_mq_lane_and_execution_plane_dispatch_mode_whitespace_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXECUTION_DISPATCH_MODE 会先 strip 再小写归一；前后空白不影响 mq 识别。"""
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "  mq  ")
    monkeypatch.setenv("MQ_BROKER_URL", "amqp://user:pass@broker:5672/vh")
    monkeypatch.delenv("MQ_TOPIC_EXECUTE", raising=False)
    monkeypatch.delenv("MQ_TOPIC_AGENT", raising=False)

    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import v1_mq_lane_health_summary

        orch = v1_mq_lane_health_summary()

    with executor_sys_path_isolated():
        from app.main import _v1_execution_plane_summary

        exe = _v1_execution_plane_summary()

    for k, v in orch.items():
        assert exe.get(k) == v, k
    assert orch["execution_dispatch_mode"] == "mq"
    assert orch["mq_dispatch_ready"] is True
