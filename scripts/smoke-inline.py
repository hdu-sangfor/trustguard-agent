from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator" / "app"))
os.environ.setdefault("TRUSTGUARD_EXECUTOR_MODE", "inline")

from trustguard_agent.graph_core import run_agent_dict  # noqa: E402


def main() -> int:
    result = run_agent_dict(
        {
            "task_id": "smoke-inline",
            "target": "https://demo.trustguard.local",
            "allowed_target": "demo.trustguard.local",
            "allowed_tools": ["http_probe", "fingerprint", "risk_review"],
        }
    )
    tool_results = [e for e in result["trace"] if e.get("type") == "tool.result"]
    assert result["status"] == "OK"
    assert len(tool_results) == 3
    assert any(f.get("severity") == "medium" for f in result["findings"])
    print("smoke-inline OK")
    print(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
