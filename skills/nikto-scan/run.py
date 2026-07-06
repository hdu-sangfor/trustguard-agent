from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    execute_py = here / "scripts" / "execute.py"
    if not execute_py.is_file():
        print(f"missing execute script: {execute_py}", file=sys.stderr, flush=True)
        return 1

    payload = sys.argv[1] if len(sys.argv) > 1 else ""
    if not payload.strip():
        payload = os.getenv("SKILL_PAYLOAD", "{}")

    argv = [sys.executable, str(execute_py), payload]
    os.execvpe(sys.executable, argv, os.environ.copy())


if __name__ == "__main__":
    raise SystemExit(main())
