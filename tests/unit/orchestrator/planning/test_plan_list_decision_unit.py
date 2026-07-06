import json
import os

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core.plan_list_decision import (  # type: ignore[import]
    PLAN_LIST_ONLY_SYSTEM_PROMPT,
    parse_plan_list_llm_output,
    plan_list_system_prompt,
)
from app.plan_models import PlanErrorCode  # type: ignore[import]


def _valid_plan_list_dict():
    return {
        "schema_version": "plan-v1",
        "task_id": "task-1",
        "batch_id": "b1",
        "items": [
            {
                "schema_version": "plan-v1",
                "plan_id": "p1",
                "task_id": "task-1",
                "skill_id": "nmap",
                "plan_content": "scan ports",
                "context_chunk_refs": [],
                "constraints": {
                    "schema_version": "plan-v1",
                    "target_scope": "host:example.com",
                    "timeout_seconds": 120,
                },
                "metadata": {},
            }
        ],
    }


def test_parse_valid_fixture():
    raw = json.dumps(_valid_plan_list_dict(), ensure_ascii=False)
    plan, err = parse_plan_list_llm_output(raw)
    assert err is None
    assert plan is not None
    assert plan.task_id == "task-1"
    assert len(plan.items) == 1
    assert plan.items[0].skill_id == "nmap"


def test_parse_valid_wrapped_in_markdown():
    inner = json.dumps(_valid_plan_list_dict())
    raw = f"Here you go:\n```json\n{inner}\n```\n"
    plan, err = parse_plan_list_llm_output(raw)
    assert err is None
    assert plan is not None
    assert plan.task_id == "task-1"


def test_parse_empty_items_ok():
    d = {"schema_version": "plan-v1", "task_id": "t", "items": []}
    plan, err = parse_plan_list_llm_output(json.dumps(d))
    assert err is None
    assert plan is not None
    assert plan.items == []


def test_reject_legacy_execute_skill_shape():
    legacy = {
        "decision": "EXECUTE_SKILL",
        "skill_id": "nmap",
        "target": "http://example.com",
        "params": {},
    }
    plan, err = parse_plan_list_llm_output(json.dumps(legacy))
    assert plan is None
    assert err is not None
    assert err.code == PlanErrorCode.INVALID_PLAN_LIST


def test_reject_invalid_json():
    plan, err = parse_plan_list_llm_output("{not json")
    assert plan is None
    assert err is not None
    assert err.code == PlanErrorCode.INVALID_PLAN_LIST
    assert err.message == "json_decode_error"
    assert "error" in err.details


def test_reject_missing_required_fields():
    bad = {"schema_version": "plan-v1", "task_id": "t", "items": [{}]}
    plan, err = parse_plan_list_llm_output(json.dumps(bad))
    assert plan is None
    assert err is not None
    assert err.code == PlanErrorCode.INVALID_PLAN_LIST


def test_prompt_mentions_planlist_and_forbids_legacy():
    assert "PlanList" in PLAN_LIST_ONLY_SYSTEM_PROMPT or "plan-v1" in PLAN_LIST_ONLY_SYSTEM_PROMPT
    assert "EXECUTE_SKILL" in PLAN_LIST_ONLY_SYSTEM_PROMPT
    assert "NEXT_PHASE" in PLAN_LIST_ONLY_SYSTEM_PROMPT
    assert "plan-v1 capability defaults" in PLAN_LIST_ONLY_SYSTEM_PROMPT


def test_plan_list_system_prompt_stays_on_v1(monkeypatch):
    s = plan_list_system_prompt()
    assert "plan-v1" in s
    assert ("plan-v" + str(2)) not in s
