"""MicroExecutor Agent 轨 stdout JSON（S-01）单测。"""
from __future__ import annotations

import json

import pytest

from tests.executor_test_env import prepare_executor_app_import

prepare_executor_app_import()

from app.micro_executor.outputs import (  # noqa: E402
    AGENT_SUMMARY_SCHEMA_VERSION,
    MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV,
    AgentSummaryJSON,
    serialize_for_agent_stdout,
)


def test_agent_stdout_budget_env_key_constant_stable() -> None:
    assert MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV == "MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS"


def test_roundtrip_compact_fits() -> None:
    body = AgentSummaryJSON(
        skill_id="nmap",
        status="SUCCESS",
        artifact_ref="wsref:task/phase/evt",
        summary="open 80/tcp",
        highlights={"ports": [80]},
    )
    s = serialize_for_agent_stdout(body, max_chars=10_000)
    d = json.loads(s)
    assert d["v"] == AGENT_SUMMARY_SCHEMA_VERSION
    assert d["skill_id"] == "nmap"
    assert d["highlights"]["ports"] == [80]
    assert d.get("content_truncated") is not True


def test_forbids_extra_keys() -> None:
    with pytest.raises(Exception):
        AgentSummaryJSON(skill_id="x", status="y", evil="injection")  # type: ignore[call-arg]


def test_truncates_long_summary() -> None:
    body = AgentSummaryJSON(
        skill_id="nmap",
        status="SUCCESS",
        summary="A" * 5000,
        highlights={},
    )
    s = serialize_for_agent_stdout(body, max_chars=400)
    assert len(s) <= 400
    d = json.loads(s)
    assert d.get("content_truncated") is True
    assert "Warning" in (d.get("warning") or "")


def test_truncates_large_highlights() -> None:
    body = AgentSummaryJSON(
        skill_id="x",
        status="OK",
        summary="ok",
        highlights={"blob": {"n": list(range(200))}},
    )
    s = serialize_for_agent_stdout(body, max_chars=280)
    d = json.loads(s)
    assert d.get("content_truncated") is True
    assert d.get("highlights") == {} or len(json.dumps(d.get("highlights"))) < 50


def test_serialize_uses_microexecutor_agent_summary_max_chars_env_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未显式传入 max_chars 时，`MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS` 决定字符预算（运维可调 S-01）。"""
    monkeypatch.setenv(MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV, "360")
    body = AgentSummaryJSON(
        skill_id="nmap",
        status="SUCCESS",
        summary="C" * 8000,
        highlights={},
    )
    s = serialize_for_agent_stdout(body)
    assert len(s) <= 360
    d = json.loads(s)
    assert d.get("content_truncated") is True


def test_serialize_invalid_env_falls_back_to_token_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV, "not-a-number")
    body = AgentSummaryJSON(skill_id="x", status="OK", summary="D" * 6000, highlights={})
    s = serialize_for_agent_stdout(body)
    assert len(s) <= 500 * 4
    assert json.loads(s).get("content_truncated") is True


def test_serialize_env_int_trimmed_of_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV, "  420  ")
    body = AgentSummaryJSON(skill_id="x", status="OK", summary="E" * 5000, highlights={})
    s = serialize_for_agent_stdout(body)
    assert len(s) <= 420
    assert json.loads(s).get("content_truncated") is True
