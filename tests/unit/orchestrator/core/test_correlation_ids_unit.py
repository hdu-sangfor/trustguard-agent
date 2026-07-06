import os
import sys

from tests.paths import REPO_ROOT

_ORCH = str(REPO_ROOT / "orchestrator")
if _ORCH not in sys.path:
    sys.path.insert(0, _ORCH)

from app.core.correlation_ids import attach_correlation, correlation_dict, correlation_log_suffix


def test_correlation_dict_omits_empty_optional():
    assert correlation_dict("t1") == {"task_id": "t1"}
    assert correlation_dict("t1", request_id="r1") == {"task_id": "t1", "request_id": "r1"}
    assert correlation_dict("t1", request_id="", plan_id="  ") == {"task_id": "t1"}


def test_attach_correlation_mutation():
    p: dict = {"phase": "RECON"}
    attach_correlation(p, "task-x", request_id="req-y", plan_id="plan-z")
    assert p["correlation"] == {"task_id": "task-x", "request_id": "req-y", "plan_id": "plan-z"}


def test_correlation_log_suffix():
    assert "task_id=t1" in correlation_log_suffix({"task_id": "t1", "request_id": "r1"})
    assert correlation_log_suffix({}) == ""
