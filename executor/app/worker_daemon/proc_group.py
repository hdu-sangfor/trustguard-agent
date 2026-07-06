"""
POSIX 子进程组（D-01 骨架）：`subprocess.Popen(..., start_new_session=True)` 使子进程成为新进程组组长，
对其 `pid` 调用 `os.killpg(pid, SIGKILL)` 可终结该组内后代（工具链）。
非 POSIX（如 Windows）无 `killpg`：仅对直子进程 `os.kill` best-effort。
"""
from __future__ import annotations

import errno
import os
import signal
from typing import Any


def _sig_hard_kill() -> int:
    """POSIX 为 SIGKILL；Windows `signal` 常无 SIGKILL，退回 SIGTERM。"""
    return getattr(signal, "SIGKILL", signal.SIGTERM)


def subprocess_session_kwargs() -> dict[str, Any]:
    """
    合并进 `subprocess.Popen(..., **subprocess_session_kwargs(), ...)`。
    POSIX：`start_new_session=True` → 子进程 `pid` 即进程组 ID。
    Windows：无进程组语义，返回空字典（由调用方沿用单进程 kill）。
    """
    if os.name == "nt":
        return {}
    return {"start_new_session": True}


def kill_process_group_leader(pid: int, sig: int | None = None) -> None:
    """
    终结由 `subprocess_session_kwargs()` 拉起的子树根（组长 = `Popen.pid`）。
    - POSIX：`os.killpg(pid, sig)`。
    - 其他平台：`os.kill(pid, sig)`（仅直接子进程）。
    `ProcessLookupError` / `ESRCH` 视为已退出，静默成功。
    """
    if pid <= 0:
        return
    if sig is None:
        sig = _sig_hard_kill()
    if os.name == "posix":
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except OSError as e:
            if e.errno in (errno.ESRCH, errno.EPERM):
                return
            raise
        return
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except OSError as e:
        if e.errno in (errno.ESRCH, errno.EPERM):
            return
        raise


def grace_then_sigkill_group_leader(pid: int, *, grace_seconds: float = 0.5) -> None:
    """
    先发 SIGTERM，等待 `grace_seconds` 后再 `SIGKILL`（仅 POSIX 对整组生效；Windows 仅针对 pid）。
    """
    if pid <= 0:
        return
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as e:
            if e.errno not in (errno.ESRCH, errno.EPERM):
                raise
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as e:
            if e.errno not in (errno.ESRCH, errno.EPERM):
                raise
    if grace_seconds > 0:
        try:
            import time

            time.sleep(grace_seconds)
        except Exception:
            pass
    kill_process_group_leader(pid)
