"""r4d：编译期 context_chunk_refs 存在性 / 租户 Fast-Fail。"""
import os
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem  # type: ignore[import]
from app.core.instruction_compiler import compile_plan_item  # type: ignore[import]


@pytest.fixture
def chunk_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _plan_with_refs(task_id: str, *refs: ContextChunkRef) -> PlanItem:
    return PlanItem(
        plan_id="p1",
        task_id=task_id,
        skill_id="ruleless-skill",
        plan_content="x",
        context_chunk_refs=list(refs),
        constraints=PlanConstraints(
            target_scope="host:example.com",
            timeout_seconds=120,
            max_parallelism=1,
        ),
        metadata={},
    )


def test_r4d_succeeds_when_chunks_exist(chunk_workspace: Path) -> None:
    from app.core import chunk_store

    cid = chunk_store.write_chunk("t-r4d", chunk_type="ctx", body={"a": 1})
    item = _plan_with_refs("t-r4d", ContextChunkRef(chunk_id=cid, tenant_id=None))
    r = compile_plan_item(item, verify_chunks=True)
    assert r.ok and r.instruction
    assert r.instruction.context_chunk_refs[0].chunk_id == cid
    assert len(r.instruction.resolved_context_chunks) == 1
    assert r.instruction.resolved_context_chunks[0].chunk_id == cid
    assert r.instruction.resolved_context_chunks[0].chunk_type == "ctx"
    assert r.instruction.resolved_context_chunks[0].body == {"a": 1}


def test_r4d_missing_chunk_not_found(chunk_workspace: Path) -> None:
    missing = "chk-" + "f" * 32
    item = _plan_with_refs("t-r4d", ContextChunkRef(chunk_id=missing, tenant_id=None))
    r = compile_plan_item(item, verify_chunks=True)
    assert r.ok is False
    assert r.error is not None
    assert r.error.code.value == "CHUNK_NOT_FOUND"
    assert r.error.details.get("suggested_action") == "retry_with_replan"
    assert r.error.details.get("chunk_id") == missing


def test_r4d_tenant_required_on_bound_chunk(chunk_workspace: Path) -> None:
    from app.core import chunk_store

    cid = chunk_store.write_chunk(
        "t-r4d",
        chunk_type="ctx",
        body={},
        tenant_id="tenant-A",
    )
    item = _plan_with_refs(
        "t-r4d",
        ContextChunkRef(chunk_id=cid, tenant_id=None),
    )
    r = compile_plan_item(item, verify_chunks=True)
    assert r.ok is False
    assert r.error is not None
    assert r.error.code.value == "CHUNK_FORBIDDEN"
    assert r.error.details.get("suggested_action") == "retry_with_replan"


def test_r4d_tenant_mismatch_forbidden(chunk_workspace: Path) -> None:
    from app.core import chunk_store

    cid = chunk_store.write_chunk(
        "t-r4d",
        chunk_type="ctx",
        body={},
        tenant_id="tenant-A",
    )
    item = _plan_with_refs(
        "t-r4d",
        ContextChunkRef(chunk_id=cid, tenant_id="other"),
    )
    r = compile_plan_item(item, verify_chunks=True)
    assert r.ok is False
    assert r.error is not None
    assert r.error.code.value == "CHUNK_FORBIDDEN"


def test_r4d_verify_disabled_skips_store(chunk_workspace: Path) -> None:
    missing = "chk-" + "0" * 32
    item = _plan_with_refs("t-r4d", ContextChunkRef(chunk_id=missing, tenant_id=None))
    r = compile_plan_item(item, verify_chunks=False)
    assert r.ok and r.instruction
    assert r.instruction.resolved_context_chunks == []


def test_kb_r5b_resolved_bundle_over_budget(chunk_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import chunk_store

    monkeypatch.setenv("ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES", "20")
    cid = chunk_store.write_chunk("t-r4d", chunk_type="ctx", body={"blob": "x" * 100})
    item = _plan_with_refs("t-r4d", ContextChunkRef(chunk_id=cid, tenant_id=None))
    r = compile_plan_item(item, verify_chunks=True)
    assert r.ok is False
    assert r.error is not None
    assert r.error.code.value == "COMPILATION_FAILED"
    assert "budget" in (r.error.message or "").lower()


def test_r4f_c_compile_cache_hit_avoids_repeated_chunk_reads(
    chunk_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import chunk_store
    from app.core import instruction_compiler as ic
    from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem

    monkeypatch.setenv("ORCH_COMPILER_ENABLE_CACHE", "true")
    ic._clear_compile_cache_for_tests()
    cid = chunk_store.write_chunk("t-r4d-cache", chunk_type="ctx", body={"k": "v"})
    item = PlanItem(
        plan_id="p1",
        task_id="t-r4d-cache",
        skill_id="ruleless-skill",
        plan_content="x",
        context_chunk_refs=[ContextChunkRef(chunk_id=cid, tenant_id=None)],
        constraints=PlanConstraints(
            target_scope="host:example.com",
            timeout_seconds=120,
            max_parallelism=1,
        ),
        metadata={},
    )

    called = {"n": 0}
    _orig = ic.read_chunk

    def _counting_read_chunk(*args, **kwargs):
        called["n"] += 1
        return _orig(*args, **kwargs)

    monkeypatch.setattr(ic, "read_chunk", _counting_read_chunk)
    r1 = ic.compile_plan_item(item, verify_chunks=True)
    r2 = ic.compile_plan_item(item, verify_chunks=True)
    assert r1.ok and r1.instruction
    assert r2.ok and r2.instruction
    assert called["n"] == 1


def test_default_resolved_context_bundle_max_bytes_is_2mib(monkeypatch: pytest.MonkeyPatch) -> None:
    """V1 红线：未设置 ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES 时默认 2MiB。"""
    monkeypatch.delenv("ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES", raising=False)
    from app.core import instruction_compiler as ic

    assert ic._resolved_context_bundle_max_bytes() == 2 * 1024 * 1024


def test_kb_r5b_resolved_accumulated_over_budget_across_chunks(
    chunk_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多 chunk 时按 body JSON 字节累计；总和超预算应在第二块失败。"""
    from app.core import chunk_store

    monkeypatch.setenv("ORCH_COMPILER_RESOLVED_CONTEXT_MAX_BYTES", "40")
    cid1 = chunk_store.write_chunk("t-r4d-acc", chunk_type="ctx", body={"a": "y" * 14})
    cid2 = chunk_store.write_chunk("t-r4d-acc", chunk_type="ctx", body={"b": "z" * 14})
    item = _plan_with_refs(
        "t-r4d-acc",
        ContextChunkRef(chunk_id=cid1, tenant_id=None),
        ContextChunkRef(chunk_id=cid2, tenant_id=None),
    )
    r = compile_plan_item(item, verify_chunks=True)
    assert r.ok is False and r.error is not None
    assert r.error.code.value == "COMPILATION_FAILED"
    assert "accumulated" in (r.error.message or "").lower() or "budget" in (r.error.message or "").lower()
    assert r.error.details.get("suggested_action") == "retry_with_replan"

