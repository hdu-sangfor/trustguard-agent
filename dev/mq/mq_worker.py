from __future__ import annotations

import asyncio
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTOR_ROOT = REPO_ROOT / "executor"
if str(EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTOR_ROOT))

from app.mq_worker import _handle_message  # noqa: E402


async def handle_message(body: bytes) -> None:
    await _handle_message(body)


if __name__ == "__main__":
    data = sys.stdin.buffer.read()
    asyncio.run(handle_message(data))
