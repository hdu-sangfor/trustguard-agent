"""r4f-e: HTTP /v1/execute JSON and MQExecuteTaskMessage share params/context."""
import json

from tests.orchestrator_test_env import prepare_orchestrator_app_import
from tests.paths import REPO_ROOT

prepare_orchestrator_app_import()

from app.clients.executor_client import build_executor_execute_json_payload  # type: ignore[import]
from app.core.agent_context import EXECUTION_CONTEXT_ALLOWED_KEYS  # type: ignore[import]
from app.core.execution_dispatcher import _extract_todo_id  # type: ignore[import]
from app.schemas.mq_execute_task import build_mq_execute_task_message  # type: ignore[import]

_SNAPSHOT_PATH = REPO_ROOT / "tests" / "snapshots" / "r4f_e_executor_payload_v1.json"


def _sample_raw_context() -> dict:
    return {
        "phase": "RECON",
        "plan_id": "p-1",
        "request_id": "req-fixed",
        "evil_injection": "should_drop",
        "nested": {"x": 1},
        "agent_role": "orchestrator",
    }


def test_r4f_e_http_json_and_mq_message_share_params_and_filtered_context() -> None:
    task_id = "t-r4f"
    skill_id = "nmap"
    target = "host:example.com"
    allowed = "host:example.com"
    params = {"ports": "80,443", "timeout": 120}
    raw_ctx = _sample_raw_context()
    request_id = "req-r4f-e"

    http = build_executor_execute_json_payload(
        task_id,
        skill_id,
        target,
        params,
        allowed_target=allowed,
        context=raw_ctx,
        request_id=request_id,
    )
    todo_id = _extract_todo_id(raw_ctx)
    mq = build_mq_execute_task_message(
        request_id=request_id,
        task_id=task_id,
        skill_id=skill_id,
        target=target,
        params=params,
        allowed_target=allowed,
        context=raw_ctx,
        todo_id=todo_id,
    )

    assert http["params"] == mq.params == params
    assert http["context"] == dict(mq.context)
    for k in ("evil_injection", "nested"):
        assert k not in http["context"]
        assert k not in mq.context
    assert set(http["context"].keys()) <= set(EXECUTION_CONTEXT_ALLOWED_KEYS)
    assert http["task_id"] == mq.task_id == task_id
    assert http["skill_id"] == mq.skill_id == skill_id
    assert http["target"] == mq.target == target
    assert http["allowed_target"] == mq.allowed_target == allowed
    assert http["execution_kind"] == "skill"
    assert mq.execution_kind == "skill"


def test_r4f_e_payload_snapshot_v1() -> None:
    task_id = "t-snapshot-r4f-e"
    skill_id = "nmap"
    target = "host:example.com"
    allowed = target
    request_id = "req-snapshot-r4f-e"
    params = {"ports": "80,443", "timeout": 120}
    raw_ctx = {
        "phase": "RECON",
        "plan_id": "p-snapshot",
        "request_id": request_id,
        "agent_role": "orchestrator",
        "evil_injection": "drop-me",
    }
    todo_id = _extract_todo_id(raw_ctx)
    http = build_executor_execute_json_payload(
        task_id,
        skill_id,
        target,
        params,
        allowed_target=allowed,
        context=raw_ctx,
        request_id=request_id,
    )
    mq = build_mq_execute_task_message(
        request_id=request_id,
        task_id=task_id,
        skill_id=skill_id,
        target=target,
        params=params,
        allowed_target=allowed,
        context=raw_ctx,
        todo_id=todo_id,
    )
    actual = {
        "snapshot_version": "r4f-e/v1",
        "http_payload": http,
        "mq_payload": mq.model_dump(mode="json"),
    }
    expected = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert actual == expected
