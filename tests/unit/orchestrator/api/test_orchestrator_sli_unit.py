"""SLI counters, snapshot and threshold alerts."""

from __future__ import annotations

import sys

import pytest

from tests.paths import REPO_ROOT

_ORCH = str(REPO_ROOT / "orchestrator")
if _ORCH not in sys.path:
    sys.path.insert(0, _ORCH)


def test_instruction_compiler_emits_sli_counters():
    from app.core.instruction_compiler import InstructionCompiler, InstructionCompileResult, _emit_compile_sli
    from app.orchestrator_sli_metrics import snapshot_counters
    from app.plan_models import (
        PlanConstraints,
        PlanErrorCode,
        PlanErrorEnvelope,
        PlanItem,
        PlanSchemaVersion,
    )

    item = PlanItem(
        schema_version=PlanSchemaVersion.V1,
        plan_id="p1",
        task_id="t1",
        skill_id="nmap",
        plan_content="scan",
        context_chunk_refs=[],
        constraints=PlanConstraints(
            schema_version=PlanSchemaVersion.V1,
            target_scope="http://127.0.0.1",
            timeout_seconds=60,
        ),
    )
    ic0 = snapshot_counters()["instruction_compile"]
    InstructionCompiler().compile(item)
    _emit_compile_sli(
        InstructionCompileResult(
            ok=False,
            error=PlanErrorEnvelope(
                schema_version=PlanSchemaVersion.V1,
                code=PlanErrorCode.COMPILATION_FAILED,
                message="synthetic fail for sli test",
                details={},
            ),
        )
    )
    snap = snapshot_counters()["instruction_compile"]
    assert snap["compile_total"] == ic0["compile_total"] + 2
    assert snap["compile_fail"] == ic0["compile_fail"] + 1
    assert "compile_duration_bucket_counts" in snap
    assert "compile_duration_samples" in snap


def test_tick_metrics_record():
    from app.orchestrator_sli_metrics import (
        record_skill_execution_result,
        record_tick_attempt,
        snapshot_counters,
    )

    o0 = snapshot_counters()["orchestrator_tick"]
    record_tick_attempt(ok=True, duration_ms=100.0)
    record_tick_attempt(ok=True, duration_ms=300.0)
    record_tick_attempt(ok=False, duration_ms=0.0)
    o = snapshot_counters()["orchestrator_tick"]
    assert o["tick_attempts_total"] == o0["tick_attempts_total"] + 3
    assert o["tick_attempts_failed"] == o0["tick_attempts_failed"] + 1
    assert o["tick_duration_ms_avg"] is not None

    se = snapshot_counters()["skill_execution"]
    t0 = int(se["execution_by_skill"].get("nmap") or 0)
    to0 = int(se["execution_timeout_by_skill"].get("nmap") or 0)
    record_skill_execution_result("nmap", "SUCCESS")
    record_skill_execution_result("nmap", "TIMEOUT")
    se2 = snapshot_counters()["skill_execution"]
    assert int(se2["execution_by_skill"].get("nmap") or 0) == t0 + 2
    assert int(se2["execution_timeout_by_skill"].get("nmap") or 0) == to0 + 1


def test_http_sli_snapshot_endpoint(monkeypatch):
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "http")
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/v1/orchestrator/sli/snapshot?include_mq=false")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == "orch-sli-v1"
    assert "alerts" in body


@pytest.mark.asyncio
async def test_sli_snapshot_shape(monkeypatch):
    monkeypatch.setenv("EXECUTION_DISPATCH_MODE", "http")
    from app.orchestrator_sli_snapshot import build_sli_snapshot

    doc = await build_sli_snapshot(include_mq=False)
    assert doc["schema_version"] == "orch-sli-v1"
    assert "instruction_compile" in doc["counters"]
    assert doc["mq_execute_queue"] is None


def test_sli_alert_eval_compile_rate(monkeypatch):
    monkeypatch.setenv("ORCH_SLI_ALERT_COMPILE_FAIL_RATE", "0.2")
    monkeypatch.setenv("ORCH_SLI_ALERT_COMPILE_MIN_SAMPLES", "5")
    from app.orchestrator_sli_snapshot import eval_sli_threshold_alerts

    ctr = {
        "instruction_compile": {
            "compile_total": 10,
            "compile_ok": 7,
            "compile_fail": 3,
            "compile_fail_rate": 0.3,
            "compile_fail_by_code": {},
        },
        "orchestrator_tick": {"tick_attempts_total": 0},
    }
    alerts = eval_sli_threshold_alerts(ctr, None)
    assert any(a["name"] == "compile_fail_rate_high" for a in alerts)
