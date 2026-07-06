"""worker_daemon.proc_group：POSIX killpg / SIGKILL 骨架与 Windows 降级。"""
from __future__ import annotations

import os
import signal
import sys
from unittest.mock import patch

import pytest

from tests.executor_test_env import executor_sys_path_isolated


def test_subprocess_session_kwargs_nt_vs_posix() -> None:
    with executor_sys_path_isolated():
        from app.worker_daemon.proc_group import subprocess_session_kwargs

        with patch.object(os, "name", "nt"):
            assert subprocess_session_kwargs() == {}
        with patch.object(os, "name", "posix"):
            assert subprocess_session_kwargs() == {"start_new_session": True}


def test_kill_process_group_leader_skips_non_positive_pid() -> None:
    with executor_sys_path_isolated():
        from app.worker_daemon.proc_group import kill_process_group_leader

        with patch(
            "app.worker_daemon.proc_group.os.killpg",
            create=True,
            side_effect=AssertionError,
        ) as kp:
            kill_process_group_leader(0)
            kill_process_group_leader(-1)
            kp.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX killpg 语义")
def test_kill_process_group_leader_posix_uses_killpg() -> None:
    with executor_sys_path_isolated():
        from app.worker_daemon.proc_group import kill_process_group_leader

        with patch.object(os, "name", "posix"):
            with patch("app.worker_daemon.proc_group.os.killpg", create=True) as kp:
                sig = getattr(signal, "SIGKILL", signal.SIGTERM)
                kill_process_group_leader(4242, sig)
                kp.assert_called_once_with(4242, sig)


@pytest.mark.skipif(sys.platform != "win32", reason="仅验证 Windows 分支走 os.kill")
def test_kill_process_group_leader_windows_uses_kill() -> None:
    with executor_sys_path_isolated():
        from app.worker_daemon.proc_group import kill_process_group_leader

        with patch("os.kill") as k:
            kill_process_group_leader(4242)
            k.assert_called_once()


def test_kill_process_group_leader_swallows_no_such_process() -> None:
    with executor_sys_path_isolated():
        from app.worker_daemon.proc_group import kill_process_group_leader

        with patch.object(os, "name", "posix"):
            with patch(
                "app.worker_daemon.proc_group.os.killpg",
                create=True,
                side_effect=ProcessLookupError,
            ):
                kill_process_group_leader(999)
