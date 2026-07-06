from __future__ import annotations

import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


ROOT = REPO_ROOT
sys.path.insert(0, str(ROOT / "orchestrator" / "app"))
os.environ.setdefault("TRUSTGUARD_EXECUTOR_MODE", "inline")

from trustguard_agent.graph_core import run_agent_dict  # noqa: E402
from trustguard_agent.models import AgentRunRequest  # noqa: E402
from trustguard_agent.graph import run_agent  # noqa: E402


def test_inline_langgraph_runtime_produces_actionable_finding() -> None:
    result = run_agent_dict(
        {
            "task_id": "t-test",
            "target": "https://demo.trustguard.local",
            "allowed_target": "demo.trustguard.local",
            "allowed_tools": ["http_probe", "fingerprint", "risk_review"],
        }
    )

    assert result["status"] == "OK"
    assert "actionable" in result["summary"].lower()
    assert any(f.get("severity") == "medium" for f in result["findings"])
    assert [e.get("type") for e in result["trace"]].count("tool.result") == 3


def test_scope_denial_is_reported_in_trace() -> None:
    result = run_agent_dict(
        {
            "task_id": "t-scope",
            "target": "https://outside.example",
            "allowed_target": "demo.trustguard.local",
            "allowed_tools": ["http_probe"],
        }
    )

    assert result["status"] == "OK"
    assert any("outside allowed scope" in str(e.get("summary", "")) for e in result["trace"])


def test_agent_api_model_path_produces_report_quality_result() -> None:
    result = run_agent(
        AgentRunRequest(
            task_id="t-api",
            target="https://demo.trustguard.local",
            allowed_target="demo.trustguard.local",
            objective="Run a concise first-pass web security assessment.",
        )
    )

    assert result.status == "OK"
    assert result.summary == "Completed 3 tactical checks and identified 1 actionable risk(s)."
    assert any(f.get("id") == "med-api-docs-exposed" for f in result.findings)
    assert result.artifacts["risk_review"]["risk_score"] == 61
