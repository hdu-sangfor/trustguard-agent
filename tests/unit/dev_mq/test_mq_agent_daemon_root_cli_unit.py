"""`dev/mq/mq_agent_daemon.py` 薄封装存在性 / 可编译性（不连 MQ）。"""
from __future__ import annotations

from tests.paths import REPO_ROOT


def test_root_mq_agent_daemon_wrapper_file_exists_and_compiles() -> None:
    root = REPO_ROOT
    script = root / "dev" / "mq" / "mq_agent_daemon.py"
    assert script.is_file(), "expected dev/mq/mq_agent_daemon.py"
    src = script.read_text(encoding="utf-8")
    compile(src, str(script), "exec")
