import json
import os

import pytest
from pydantic import ValidationError

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem  # type: ignore[import]
from app.plan_validation import validate_compiled_instruction  # type: ignore[import]
from app.core.instruction_compiler import (  # type: ignore[import]
    InstructionCompileResult,
    InstructionCompiler,
    compile_plan_item,
)


def _item(
    *,
    target_scope: str = "host:example.com",
    skill_id: str = "ruleless-skill",
    metadata: dict | None = None,
    timeout_seconds: int = 120,
) -> PlanItem:
    return PlanItem(
        plan_id="p1",
        task_id="t1",
        skill_id=skill_id,
        plan_content="scan",
        context_chunk_refs=[],
        constraints=PlanConstraints(
            target_scope=target_scope,
            timeout_seconds=timeout_seconds,
            max_parallelism=2,
        ),
        metadata=metadata or {},
    )


def test_compile_uses_target_scope_by_default() -> None:
    item = _item()
    r = compile_plan_item(item)
    assert r.ok is True
    assert r.instruction is not None
    assert r.instruction.target == "host:example.com"
    assert r.instruction.skill_id == "ruleless-skill"
    assert r.instruction.timeout_seconds == 120
    assert r.instruction.context_chunk_refs == []
    assert r.instruction.resolved_context_chunks == []
    assert r.instruction.params == {}

    payload = json.loads(r.instruction.model_dump_json())
    ok, err = validate_compiled_instruction(payload)
    assert ok and err is None


def test_execution_target_overrides_scope() -> None:
    item = _item()
    r = compile_plan_item(item, execution_target="https://example.com/")
    assert r.ok and r.instruction
    assert r.instruction.target == "https://example.com/"


def test_compile_fails_when_resolved_target_empty() -> None:
    item = _item(target_scope="   ")
    r = compile_plan_item(item, execution_target="  ")
    assert r.ok is False
    assert r.error is not None
    assert r.error.code.value == "COMPILATION_FAILED"


def test_explicit_compiler_instance() -> None:
    c = InstructionCompiler()
    r = compile_plan_item(_item(), compiler=c)
    assert r.ok


def test_compile_result_model_enforces_consistency() -> None:
    with pytest.raises(ValidationError):
        InstructionCompileResult(ok=True, instruction=None)


def test_nmap_rule_merge_metadata_params() -> None:
    item = _item(
        skill_id="nmap",
        metadata={
            "params": {"ports": "22,80,443", "evil": "drop-me"},
            "nmap_params": {"top_ports": 100},
        },
        timeout_seconds=300,
    )
    r = compile_plan_item(item)
    assert r.ok and r.instruction
    p = r.instruction.params
    assert p["ports"] == "22,80,443"
    assert p["top_ports"] == 100
    assert "evil" not in p
    assert p["timeout"] == 120
    assert r.instruction.timeout_seconds == 120


def test_nmap_rule_default_timeout_capped() -> None:
    item = _item(skill_id="nmap", timeout_seconds=60)
    r = compile_plan_item(item)
    assert r.ok and r.instruction
    assert r.instruction.params["timeout"] == 60


def test_nmap_skill_id_case_insensitive() -> None:
    item = _item(skill_id="NMap", timeout_seconds=90)
    r = compile_plan_item(item)
    assert r.ok and r.instruction
    assert r.instruction.params["timeout"] == 90


def test_r4c_trims_very_long_target() -> None:
    long = "https://x.example/" + ("a" * 5000)
    item = _item(target_scope=long)
    r = compile_plan_item(item)
    assert r.ok and r.instruction
    assert len(r.instruction.target) <= 4096


def test_r4c_nmap_params_timeout_trimmed() -> None:
    item = _item(
        skill_id="nmap",
        metadata={"params": {"timeout": 999}},
        timeout_seconds=600,
    )
    r = compile_plan_item(item)
    assert r.ok and r.instruction
    assert r.instruction.params["timeout"] == 120


def test_r4c_validate_compiled_failure_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plan_models import PlanErrorCode, PlanErrorEnvelope, PlanSchemaVersion

    def _fail(payload):
        return False, PlanErrorEnvelope(
            schema_version=PlanSchemaVersion.V1,
            code=PlanErrorCode.COMPILATION_FAILED,
            message="r1b-stub-fail",
            details={"stub": True},
        )

    monkeypatch.setattr("app.plan_validation.validate_compiled_instruction", _fail)
    r = compile_plan_item(_item())
    assert r.ok is False
    assert r.error is not None
    assert r.error.message == "r1b-stub-fail"
