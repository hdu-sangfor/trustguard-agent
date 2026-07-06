"""r4g-c：Executor MQ 消息处理（app.mq_worker._handle_message → mq_execute_consumer）E2E-lite。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

_ROOT = REPO_ROOT
_EXECUTOR = str(_ROOT / "executor")


def _clear_executor_app_modules() -> None:
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_r4g_c_executor_mq_worker_handle_message_e2e_lite(monkeypatch) -> None:
    _clear_executor_app_modules()
    sys.path.insert(0, _EXECUTOR)
    try:
        from app.models import SkillResult  # type: ignore[import]
        from app import mq_execute_consumer as mq_cons  # type: ignore[import]
        from app import mq_worker  # type: ignore[import]
        from app import execution_store as es  # type: ignore[import]
        from app import main as ex_main  # type: ignore[import]

        starts: list[tuple[str, str, str]] = []
        finishes: list[tuple[str, str, str]] = []

        async def _fake_start(request_id: str, task_id: str, skill_id: str, todo_id=None):
            starts.append((request_id, task_id, skill_id))
            return True

        async def _fake_finish(
            request_id: str,
            task_id: str,
            status: str,
            artifact_ref=None,
            worker_id=None,
            artifact_refs_v1=None,
            **_: object,
        ):
            finishes.append((request_id, task_id, status))
            return None

        async def _fake_execute_impl(req, artifact_sniff_pool=None, **_: object):
            return SkillResult(
                status="SUCCESS",
                parsed_artifacts={"skill_id": req.skill_id, "artifact_ref": "wsref:t/RECON/evt"},
                raw_stdout="",
                raw_stderr="",
                duration_ms=1,
            )

        monkeypatch.setattr(es, "register_execution_start", _fake_start)
        monkeypatch.setattr(es, "register_execution_finish", _fake_finish)
        monkeypatch.setattr(ex_main, "_execute_impl", _fake_execute_impl)
        monkeypatch.setattr(mq_cons, "read_artifact", lambda artifact_ref, include_raw=False: {"parsed": {"ok": 1}})

        msg = {
            "request_id": "req-r4gc-mq-1",
            "task_id": "t-r4gc-mq",
            "skill_id": "nmap",
            "target": "https://example.com",
            "params": {"timeout": 5},
            "allowed_target": "example.com",
            "context": {"phase": "RECON"},
            "todo_id": None,
        }
        asyncio.run(mq_worker._handle_message(json.dumps(msg, ensure_ascii=False).encode("utf-8")))

        assert starts == [("req-r4gc-mq-1", "t-r4gc-mq", "nmap")]
        assert finishes and finishes[0][2] == "SUCCESS"
    finally:
        if _EXECUTOR in sys.path:
            sys.path.remove(_EXECUTOR)
        _clear_executor_app_modules()


def test_r4g_c_executor_mq_worker_duplicate_request_id_skips_execution(monkeypatch) -> None:
    _clear_executor_app_modules()
    sys.path.insert(0, _EXECUTOR)
    try:
        from app import mq_worker  # type: ignore[import]
        from app import execution_store as es  # type: ignore[import]
        from app import main as ex_main  # type: ignore[import]

        executed = {"n": 0}
        finished = {"n": 0}

        async def _fake_start(request_id: str, task_id: str, skill_id: str, todo_id=None):
            return False  # duplicate / replay

        async def _fake_finish(
            request_id: str,
            task_id: str,
            status: str,
            artifact_ref=None,
            worker_id=None,
            artifact_refs_v1=None,
            **_: object,
        ):
            finished["n"] += 1
            return None

        async def _fake_execute_impl(req, artifact_sniff_pool=None, **_: object):
            executed["n"] += 1
            raise AssertionError("should not execute on duplicate request_id")

        monkeypatch.setattr(es, "register_execution_start", _fake_start)
        monkeypatch.setattr(es, "register_execution_finish", _fake_finish)
        monkeypatch.setattr(ex_main, "_execute_impl", _fake_execute_impl)

        msg = {
            "request_id": "req-r4gc-mq-dup",
            "task_id": "t-r4gc-mq",
            "skill_id": "nmap",
            "target": "https://example.com",
            "params": {"timeout": 5},
            "allowed_target": "example.com",
            "context": {"phase": "RECON"},
            "todo_id": None,
        }
        asyncio.run(mq_worker._handle_message(json.dumps(msg, ensure_ascii=False).encode("utf-8")))
        assert executed["n"] == 0
        assert finished["n"] == 0
    finally:
        if _EXECUTOR in sys.path:
            sys.path.remove(_EXECUTOR)
        _clear_executor_app_modules()
