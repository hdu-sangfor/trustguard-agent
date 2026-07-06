import json
import os
from pathlib import Path

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.plan_models import (  # type: ignore[import]
    CompiledInstruction,
    ContextChunkRef,
    PlanConstraints,
    PlanItem,
    PlanList,
    PlanSchemaVersion,
)
from app.plan_validation import (  # type: ignore[import]
    validate_compiled_instruction,
    validate_plan_item,
    validate_plan_list,
)


def _make_valid_plan_item(task_id: str = "t1", plan_id: str = "p1") -> PlanItem:
    return PlanItem(
        plan_id=plan_id,
        task_id=task_id,
        skill_id="nmap",
        plan_content="对目标进行端口扫描",
        context_chunk_refs=[
            ContextChunkRef(chunk_id="chunk-1", tenant_id="tenant-1"),
        ],
        constraints=PlanConstraints(
            target_scope="host:example.com",
            timeout_seconds=300,
            max_parallelism=4,
        ),
        metadata={"source": "unit-test"},
    )


def test_plan_item_roundtrip_and_validation_success():
    item = _make_valid_plan_item()
    payload = json.loads(item.model_dump_json())

    ok, err = validate_plan_item(payload)
    assert ok is True
    assert err is None

    # 确认可以重新加载为 PlanItem 且字段一致
    parsed = PlanItem.model_validate(payload)
    assert parsed.plan_id == item.plan_id
    assert parsed.task_id == item.task_id
    assert parsed.skill_id == item.skill_id
    assert parsed.constraints.target_scope == item.constraints.target_scope


def test_plan_item_validation_fails_on_missing_required_fields():
    # 缺少 plan_id 与 constraints 等必填字段
    raw = {
        "task_id": "t1",
        "skill_id": "nmap",
        "plan_content": "missing required fields",
    }
    ok, err = validate_plan_item(raw)
    assert ok is False
    assert err is not None
    assert err.code.value == "INVALID_PLAN_ITEM"
    assert err.details.get("errors")


def test_plan_list_v1_top_level_kit_roundtrip_and_validation():
    item = PlanItem(
        schema_version=PlanSchemaVersion.V1,
        plan_id="p-v1",
        task_id="t-v1",
        skill_id="httpx",
        plan_content="surface check",
        context_chunk_refs=[],
        constraints=PlanConstraints(target_scope="https://example.com/", timeout_seconds=120),
    )
    pl = PlanList(
        schema_version=PlanSchemaVersion.V1,
        task_id="t-v1",
        kit_id="web-recon-v1",
        tactical_goal="Map HTTP surface",
        items=[item],
    )
    payload = json.loads(pl.model_dump_json())
    ok, err = validate_plan_list(payload)
    assert ok and err is None
    back = PlanList.model_validate(payload)
    assert back.schema_version == PlanSchemaVersion.V1
    assert back.kit_id == "web-recon-v1"
    assert back.tactical_goal == "Map HTTP surface"


def test_plan_list_validation_success_and_invalid_item_detected():
    item = _make_valid_plan_item(task_id="t-list", plan_id="p-list-1")
    plan_list = PlanList(task_id="t-list", items=[item])
    payload = json.loads(plan_list.model_dump_json())

    ok, err = validate_plan_list(payload)
    assert ok is True
    assert err is None

    # 破坏内部结构，使 PlanList 校验失败
    broken = dict(payload)
    broken["items"] = [{"skill_id": "nmap"}]  # 缺少 plan_id/task_id/constraints 等
    ok2, err2 = validate_plan_list(broken)
    assert ok2 is False
    assert err2 is not None
    assert err2.code.value == "INVALID_PLAN_LIST"
    assert err2.details.get("errors")


def test_compiled_instruction_validation_success_and_failure():
    base_item = _make_valid_plan_item(task_id="t-ci", plan_id="p-ci-1")
    compiled = CompiledInstruction(
        task_id=base_item.task_id,
        plan_id=base_item.plan_id,
        skill_id=base_item.skill_id,
        target="http://example.com",
        params={"ports": "80,443"},
        timeout_seconds=120,
        context_chunk_refs=base_item.context_chunk_refs,
        resolved_context_chunks=[],
    )
    payload = json.loads(compiled.model_dump_json())

    ok, err = validate_compiled_instruction(payload)
    assert ok is True
    assert err is None

    # 缺少必填字段时应失败
    broken = {"task_id": "t-ci"}
    ok2, err2 = validate_compiled_instruction(broken)
    assert ok2 is False
    assert err2 is not None
    assert err2.code.value == "COMPILATION_FAILED"
    assert err2.details.get("errors")

