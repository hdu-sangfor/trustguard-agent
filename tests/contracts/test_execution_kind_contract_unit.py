"""execution_kind: only the native skill execution path remains."""
from __future__ import annotations

import sys
from contextlib import contextmanager

import pytest

from tests.paths import REPO_ROOT


@contextmanager
def _executor_env():
    ex = str(REPO_ROOT / "executor")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, ex)
    try:
        yield
    finally:
        if ex in sys.path:
            sys.path.remove(ex)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_executor_skillrequest_defaults_to_skill():
    with _executor_env():
        from app.models import SkillRequest

        r = SkillRequest(
            task_id="t",
            skill_id="nmap",
            target="http://a",
            params={},
            allowed_target="http://a",
        )
        assert r.execution_kind == "skill"


def test_executor_skillrequest_rejects_agent_execution_kind():
    with _executor_env():
        from app.models import SkillRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SkillRequest(
                task_id="t",
                skill_id="nmap",
                target="http://a",
                params={},
                allowed_target="http://a",
                execution_kind="agent",
            )


def test_executor_mq_message_backward_compatible_without_execution_kind_in_json():
    with _executor_env():
        from app.schemas.mq_execute_task import validate_mq_execute_task_message

        msg = validate_mq_execute_task_message(
            {
                "request_id": "r1",
                "task_id": "t1",
                "skill_id": "nmap",
                "target": "http://x",
                "allowed_target": "http://x",
                "params": {},
                "context": {},
            }
        )
        assert msg.execution_kind == "skill"


def test_resolve_execution_kind_parity_executor_vs_orchestrator():
    with _executor_env():
        from app.execution_kind import resolve_execution_kind as r_ex

        assert r_ex(skill_id="nmap", execution_kind=None) == "skill"
        assert r_ex(skill_id="nmap", execution_kind="skill") == "skill"
        with pytest.raises(ValueError):
            r_ex(skill_id="nmap", execution_kind="agent")

    from tests.orchestrator_test_env import prepare_orchestrator_app_import

    prepare_orchestrator_app_import()
    from app.core.execution_kind import resolve_execution_kind as r_orch

    assert r_orch(skill_id="nmap", execution_kind=None) == "skill"
    assert r_orch(skill_id="nmap", execution_kind="skill") == "skill"
    with pytest.raises(ValueError):
        r_orch(skill_id="nmap", execution_kind="agent")


def test_orchestrator_mq_build_sets_skill_execution_kind_explicit():
    from tests.orchestrator_test_env import prepare_orchestrator_app_import

    prepare_orchestrator_app_import()
    from app.schemas.mq_execute_task import build_mq_execute_task_message

    msg = build_mq_execute_task_message(
        request_id="r",
        task_id="t",
        skill_id="nmap",
        target="http://a",
        params={},
        allowed_target="http://a",
        context={"phase": "RECON"},
        todo_id=None,
        execution_kind="skill",
    )
    assert msg.execution_kind == "skill"
