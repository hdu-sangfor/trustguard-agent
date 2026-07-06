from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_APP = ROOT / "orchestrator" / "app"
sys.path.insert(0, str(AGENT_APP))
os.environ.setdefault("TRUSTGUARD_EXECUTOR_MODE", "inline")

from trustguard_agent.graph_core import run_agent_dict  # noqa: E402


def main() -> int:
    result = run_agent_dict(
        {
            "task_id": "demo-inline",
            "target": "https://demo.trustguard.local",
            "allowed_target": "demo.trustguard.local",
            "objective": "Run a concise first-pass web security assessment.",
            "allowed_tools": ["http_probe", "fingerprint", "risk_review"],
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "OK" and result["findings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
