"""
Daemon 托管子进程（D-01）：`Popen` + 新会话（进程组）+ stderr 行喂入 `ArtifactSniffPool` + 超时 `grace_then_sigkill_group_leader`。

stdout 默认丢入 `DEVNULL`，避免管道塞满导致死锁；需要捕获 stdout 时传入 `stdout_target=subprocess.PIPE` 并由调用方另开线程排空。
"""
from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Sequence

from app.worker_daemon.proc_group import (
    grace_then_sigkill_group_leader,
    kill_process_group_leader,
    subprocess_session_kwargs,
)
from app.worker_daemon.sniff_pool import ArtifactSniffPool


@dataclass(frozen=True)
class ManagedSessionResult:
    """托管会话终态。"""

    returncode: int
    timed_out: bool
    pid: int


def run_managed_session(
    argv: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    sniff_pool: ArtifactSniffPool | None = None,
    timeout_seconds: float | None = None,
    kill_grace_seconds: float = 0.5,
    stdout_target: int | None = subprocess.DEVNULL,
) -> ManagedSessionResult:
    """
    拉起子进程（POSIX 新进程组），后台线程将 stderr 按行送入 `sniff_pool`（若有）。

    - `timeout_seconds` 为 None 或 ``<= 0``：不启用 OS 超时（由上层控）。
    - 超时：``SIGTERM`` 宽限期后 ``SIGKILL`` 清理进程组（Windows 仅为直子进程）。
    """
    if not argv:
        raise ValueError("argv must be non-empty")
    session_kw = subprocess_session_kwargs()
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=stdout_target,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        **session_kw,
    )
    pid = int(proc.pid or 0)

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                if sniff_pool is not None:
                    sniff_pool.ingest_line(line)
        except Exception:
            pass

    drain = threading.Thread(target=_drain_stderr, name="daemon-stderr-sniff", daemon=True)
    drain.start()

    timed_out = False
    rc: int | None = None
    try:
        if timeout_seconds is not None and timeout_seconds > 0:
            rc = proc.wait(timeout=timeout_seconds)
        else:
            rc = proc.wait()
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                rc = proc.wait(timeout=15.0)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    rc = proc.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    rc = -1
        else:
            grace_then_sigkill_group_leader(pid, grace_seconds=kill_grace_seconds)
            try:
                rc = proc.wait(timeout=30.0)
            except subprocess.TimeoutExpired:
                kill_process_group_leader(pid)
                try:
                    rc = proc.wait(timeout=15.0)
                except subprocess.TimeoutExpired:
                    rc = -1
    finally:
        try:
            if proc.stderr is not None and hasattr(proc.stderr, "closed") and not proc.stderr.closed:
                proc.stderr.close()
        except Exception:
            pass
        drain.join(timeout=5.0)

    if rc is None:
        rc = -1
    return ManagedSessionResult(returncode=int(rc), timed_out=timed_out, pid=pid)
