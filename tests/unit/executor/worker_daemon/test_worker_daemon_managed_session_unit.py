"""worker_daemon.managed_session：stderr 嗅探 + 超时杀进程组。"""
from __future__ import annotations

import sys

import pytest

from tests.executor_test_env import executor_sys_path_isolated


def test_run_managed_session_sniffs_stderr_into_pool() -> None:
    with executor_sys_path_isolated():
        from app.micro_executor.protocol import build_artifact_notice
        from app.worker_daemon.managed_session import run_managed_session
        from app.worker_daemon.sniff_pool import ArtifactSniffPool

        notice = build_artifact_notice("wsref:demo/a", skill_id="v1-micro-sample", request_id="r1")
        prog = (
            "import sys\n"
            f"sys.stderr.write({notice!r} + chr(10))\n"
            "sys.stderr.flush()\n"
        )
        pool = ArtifactSniffPool(request_id="r1")
        r = run_managed_session(
            [sys.executable, "-c", prog],
            sniff_pool=pool,
            timeout_seconds=30.0,
        )
        assert r.timed_out is False
        assert r.returncode == 0
        assert pool.artifact_refs_ordered() == ["wsref:demo/a"]


def test_run_managed_session_timeout_kills_busy_child() -> None:
    with executor_sys_path_isolated():
        from app.worker_daemon.managed_session import run_managed_session

        prog = "import time\nwhile True:\n    time.sleep(1)\n"
        r = run_managed_session(
            [sys.executable, "-c", prog],
            sniff_pool=None,
            timeout_seconds=0.35,
            kill_grace_seconds=0.05,
        )
        assert r.timed_out is True


def test_run_managed_session_rejects_empty_argv() -> None:
    with executor_sys_path_isolated():
        from app.worker_daemon.managed_session import run_managed_session

        with pytest.raises(ValueError, match="argv"):
            run_managed_session([], timeout_seconds=1.0)
