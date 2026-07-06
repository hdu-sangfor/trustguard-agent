from __future__ import annotations

import logging
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTOR_ROOT = REPO_ROOT / "executor"
if str(EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTOR_ROOT))

from app.mq_agent_daemon import run_agent_daemon  # noqa: E402


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_agent_daemon()
