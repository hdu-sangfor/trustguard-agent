"""MicroExecutor 目标范围校验单测（与 executor main._is_target_allowed 语义对齐）。"""
from __future__ import annotations

import pytest

from tests.executor_test_env import prepare_executor_app_import

prepare_executor_app_import()

from app.micro_executor.target_scope import (  # noqa: E402
    TargetScopeError,
    TargetScopeValidator,
)


def test_same_host_url_variants() -> None:
    assert TargetScopeValidator.is_allowed(
        "https://example.com:443/foo",
        "http://example.com:80/",
        skill_id="nmap",
        localhost_alias="host.docker.internal",
    )


def test_localhost_normalizes_to_alias() -> None:
    assert TargetScopeValidator.is_allowed(
        "http://127.0.0.1:8080",
        "http://host.docker.internal:8080",
        skill_id="nmap",
        localhost_alias="host.docker.internal",
    )


def test_different_host_rejected() -> None:
    assert not TargetScopeValidator.is_allowed(
        "http://evil.com",
        "http://client.com",
        skill_id="nmap",
    )


def test_validate_raises() -> None:
    with pytest.raises(TargetScopeError):
        TargetScopeValidator.validate(
            "http://evil.com",
            "http://client.com",
            skill_id="nmap",
        )


def test_search_skill_bypass() -> None:
    assert TargetScopeValidator.is_allowed(
        "任意查询串",
        "http://client.com",
        skill_id="baidu-search",
    )


def test_empty_allowed_allows() -> None:
    assert TargetScopeValidator.is_allowed("http://anywhere.com", None, skill_id="nmap")
    assert TargetScopeValidator.is_allowed("http://anywhere.com", "  ", skill_id="nmap")
