"""MicroExecutor 旁路嗅探协议单测（executor/app/micro_executor/protocol.py）。"""
from __future__ import annotations

import pytest

from tests.executor_test_env import prepare_executor_app_import

prepare_executor_app_import()

from app.micro_executor.protocol import (  # noqa: E402
    ARTIFACT_NOTICE_PREFIX,
    build_artifact_notice,
    parse_artifact_notice_line,
)


def test_build_and_parse_roundtrip() -> None:
    line = build_artifact_notice(
        "ref-abc",
        skill_id="nmap",
        type_tag="RECON",
        request_id="req-1",
    )
    assert line.startswith(ARTIFACT_NOTICE_PREFIX)
    d = parse_artifact_notice_line(line)
    assert d is not None
    assert d["artifact_ref"] == "ref-abc"
    assert d["skill_id"] == "nmap"
    assert d["type_tag"] == "RECON"
    assert d["request_id"] == "req-1"


def test_parse_rejects_garbage() -> None:
    assert parse_artifact_notice_line("") is None
    assert parse_artifact_notice_line("random") is None
    assert parse_artifact_notice_line(f"{ARTIFACT_NOTICE_PREFIX}{{") is None
    assert parse_artifact_notice_line(f'{ARTIFACT_NOTICE_PREFIX}{{"v":1}}') is None


def test_build_rejects_empty_ref() -> None:
    with pytest.raises(ValueError):
        build_artifact_notice("")
    with pytest.raises(ValueError):
        build_artifact_notice("   ")
