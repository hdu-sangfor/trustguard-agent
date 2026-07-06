"""Worker Daemon：MQ + 子进程 + 旁路嗅探（分模块落地）。"""

from app.worker_daemon.managed_session import ManagedSessionResult, run_managed_session
from app.worker_daemon.proc_group import (
    grace_then_sigkill_group_leader,
    kill_process_group_leader,
    subprocess_session_kwargs,
)
from app.worker_daemon.sniff_pool import ArtifactSniffPool, SniffedArtifactRecord

__all__ = [
    "ArtifactSniffPool",
    "ManagedSessionResult",
    "SniffedArtifactRecord",
    "grace_then_sigkill_group_leader",
    "kill_process_group_leader",
    "run_managed_session",
    "subprocess_session_kwargs",
]
