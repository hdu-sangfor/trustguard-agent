"""mq_agent_daemon / mq_execute_consumer：MQ 解析、幂等、_execute_impl、register_execution_finish。"""
from __future__ import annotations

import json

import pytest

from tests.executor_test_env import executor_sys_path_isolated


@pytest.mark.asyncio
async def test_handle_agent_message_finish_success(monkeypatch: pytest.MonkeyPatch) -> None:
    with executor_sys_path_isolated():
        import app.execution_store as es
        import app.main as main_mod
        from app.mq_agent_daemon import _handle_agent_message
        from app.models import SkillResult

        finished: dict = {}

        async def fake_start(**kwargs):
            return True

        async def fake_finish(**kwargs):
            finished.update(kwargs)

        async def fake_execute(req, artifact_sniff_pool=None):
            return SkillResult(
                status="SUCCESS",
                parsed_artifacts={"artifact_ref": "wsref:t/x/y", "ok": 1},
                raw_stdout="",
                raw_stderr="",
                duration_ms=1,
            )

        import app.mq_execute_consumer as mec

        monkeypatch.setattr(es, "register_execution_start", fake_start)
        monkeypatch.setattr(es, "register_execution_finish", fake_finish)
        monkeypatch.setattr(main_mod, "_execute_impl", fake_execute)
        monkeypatch.setattr(
            mec,
            "read_artifact",
            lambda artifact_ref, include_raw=False: {"parsed": {"ok": 1}},
        )

        payload = {
            "request_id": "r-d",
            "task_id": "t-d",
            "skill_id": "nmap",
            "target": "http://example.com",
            "allowed_target": "http://example.com",
            "params": {},
            "context": {},
        }
        await _handle_agent_message(json.dumps(payload).encode("utf-8"))
        assert finished.get("status") == "SUCCESS"
        assert finished.get("request_id") == "r-d"
        assert finished.get("task_id") == "t-d"


@pytest.mark.asyncio
async def test_handle_agent_message_timeout_status(monkeypatch: pytest.MonkeyPatch) -> None:
    with executor_sys_path_isolated():
        import app.execution_store as es
        import app.main as main_mod
        from app.mq_agent_daemon import _handle_agent_message
        from app.models import SkillResult

        finished: dict = {}

        async def fake_start(**kwargs):
            return True

        async def fake_finish(**kwargs):
            finished.update(kwargs)

        async def fake_execute(req, artifact_sniff_pool=None):
            return SkillResult(
                status="TIMEOUT",
                parsed_artifacts={},
                raw_stdout="",
                raw_stderr="",
                duration_ms=1,
            )

        monkeypatch.setattr(es, "register_execution_start", fake_start)
        monkeypatch.setattr(es, "register_execution_finish", fake_finish)
        monkeypatch.setattr(main_mod, "_execute_impl", fake_execute)

        payload = {
            "request_id": "r-t",
            "task_id": "t-t",
            "skill_id": "nmap",
            "target": "http://example.com",
            "allowed_target": "http://example.com",
            "params": {},
            "context": {},
        }
        await _handle_agent_message(json.dumps(payload).encode("utf-8"))
        assert finished.get("status") == "TIMEOUT"


@pytest.mark.asyncio
async def test_handle_agent_message_skip_when_start_false(monkeypatch: pytest.MonkeyPatch) -> None:
    with executor_sys_path_isolated():
        import app.execution_store as es
        from app.mq_agent_daemon import _handle_agent_message

        async def fake_start(**kwargs):
            return False

        called = []

        async def fake_finish(**kwargs):
            called.append(True)

        monkeypatch.setattr(es, "register_execution_start", fake_start)
        monkeypatch.setattr(es, "register_execution_finish", fake_finish)

        payload = {
            "request_id": "r-skip",
            "task_id": "t-skip",
            "skill_id": "nmap",
            "target": "http://example.com",
            "allowed_target": "http://example.com",
            "params": {},
            "context": {},
        }
        await _handle_agent_message(json.dumps(payload).encode("utf-8"))
        assert called == []
