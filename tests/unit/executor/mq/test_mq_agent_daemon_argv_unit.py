"""mq_agent_daemon argv：Pydantic 校验仍用于其它场景；daemon 本体不再读取 MQ_AGENT_DAEMON_ARGV_JSON。"""
from __future__ import annotations

import json

import pytest

from tests.executor_test_env import executor_sys_path_isolated


def test_parse_mq_agent_daemon_argv_accepts_valid_array() -> None:
    with executor_sys_path_isolated():
        from app.schemas.mq_agent_daemon_argv import parse_mq_agent_daemon_argv_json

        assert parse_mq_agent_daemon_argv_json(json.dumps(["a", "b"])) == ["a", "b"]


def test_parse_mq_agent_daemon_argv_rejects_empty_or_wrong_shape() -> None:
    with executor_sys_path_isolated():
        from app.schemas.mq_agent_daemon_argv import parse_mq_agent_daemon_argv_json

        assert parse_mq_agent_daemon_argv_json("") is None
        assert parse_mq_agent_daemon_argv_json(" [] ") is None
        assert parse_mq_agent_daemon_argv_json('["x", 1]') is None
        assert parse_mq_agent_daemon_argv_json("{}") is None
        assert parse_mq_agent_daemon_argv_json("not-json") is None
