from __future__ import annotations

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def test_summarize_skill_runpy_migration_counts_pending(tmp_path: Path) -> None:
    from v1_final_form_readiness import summarize_skill_runpy_migration

    skills_root = tmp_path / "skills"
    (skills_root / "nmap" / "scripts").mkdir(parents=True)
    (skills_root / "nmap" / "scripts" / "execute.py").write_text("print('nmap')", encoding="utf-8")
    (skills_root / "sqlmap" / "scripts").mkdir(parents=True)
    (skills_root / "sqlmap" / "scripts" / "execute.py").write_text("print('sqlmap')", encoding="utf-8")
    (skills_root / "nmap" / "run.py").write_text("print('run')", encoding="utf-8")

    summary = summarize_skill_runpy_migration(skills_root)
    assert summary["baseline_skill_count"] == 2
    assert summary["migrated_skill_count"] == 1
    assert summary["pending_skill_ids"] == ["sqlmap"]
    assert summary["runpy_coverage"] == 0.5


def test_collect_v1_final_form_errors_empty_when_ready() -> None:
    from v1_final_form_readiness import collect_v1_final_form_errors

    orch = {
        "status": "ok",
        "v1_mq_lanes": {
            "execution_dispatch_mode": "mq",
            "mq_topic_execute": "execute_tasks_agent",
            "mq_topic_agent": "execute_tasks_agent",
            "mq_broker_configured": True,
            "mq_dispatch_ready": True,
            "agent_lane_mq_enabled": True,
            "agent_lane_allowlist_count": 0,
            "agent_lane_routing_active": True,
            "agent_lane_publish_ready": True,
        },
    }
    exe = {
        "status": "ok",
        "v1_execution_plane": {
            "schema_version": "v1-execution-plane-v1",
            "execution_dispatch_mode": "mq",
            "mq_topic_execute": "execute_tasks_agent",
            "mq_topic_agent": "execute_tasks_agent",
            "mq_broker_configured": True,
            "mq_dispatch_ready": True,
            "agent_lane_mq_enabled": True,
            "agent_lane_allowlist_count": 0,
            "agent_lane_routing_active": True,
            "agent_lane_publish_ready": True,
            "agent_stdout_summary_default_max_chars": 2000,
        },
    }
    summary = {
        "baseline_skill_count": 2,
        "migrated_skill_count": 2,
        "pending_skill_ids": [],
        "runpy_coverage": 1.0,
    }

    assert collect_v1_final_form_errors(orch, exe, summary, min_runpy_coverage=1.0) == []


def test_collect_v1_final_form_errors_reports_parity_and_migration_gap() -> None:
    from v1_final_form_readiness import collect_v1_final_form_errors

    orch = {
        "status": "ok",
        "v1_mq_lanes": {
            "execution_dispatch_mode": "mq",
            "mq_topic_execute": "fast",
            "mq_topic_agent": "agent",
            "mq_broker_configured": True,
            "mq_dispatch_ready": True,
            "agent_lane_mq_enabled": True,
            "agent_lane_allowlist_count": 1,
            "agent_lane_routing_active": True,
            "agent_lane_publish_ready": True,
        },
    }
    exe = {
        "status": "ok",
        "v1_execution_plane": {
            "schema_version": "v1-execution-plane-v1",
            "execution_dispatch_mode": "http",
            "mq_topic_execute": "fast",
            "mq_topic_agent": "agent",
            "mq_broker_configured": False,
            "mq_dispatch_ready": False,
            "agent_lane_mq_enabled": True,
            "agent_lane_allowlist_count": 1,
            "agent_lane_routing_active": False,
            "agent_lane_publish_ready": False,
            "agent_stdout_summary_default_max_chars": 2000,
        },
    }
    summary = {
        "baseline_skill_count": 3,
        "migrated_skill_count": 1,
        "pending_skill_ids": ["sqlmap", "nmap"],
        "runpy_coverage": 1 / 3,
    }
    errors = collect_v1_final_form_errors(orch, exe, summary, min_runpy_coverage=0.9)
    assert any("lane parity mismatch execution_dispatch_mode" in e for e in errors)
    assert any("run.py migration not ready" in e for e in errors)
