"""instruction_compiler: CHUNK_INVALID_CHUNK_ID must fail compilation (strict; no silent skip)."""
from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import  # type: ignore[import]

prepare_orchestrator_app_import()

from app.core.instruction_compiler import compile_plan_item, _clear_compile_cache_for_tests  # type: ignore[import]
from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem  # type: ignore[import]


@pytest.fixture(autouse=True)
def _clear_cache():
    _clear_compile_cache_for_tests()
    yield
    _clear_compile_cache_for_tests()


@pytest.fixture
def chunk_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _item(task_id, *refs):
    return PlanItem(
        plan_id="p-skip-inv",
        task_id=task_id,
        skill_id="ruleless-skill",
        plan_content="intent text",
        context_chunk_refs=list(refs),
        constraints=PlanConstraints(
            target_scope="host:example.com",
            timeout_seconds=120,
        ),
    )


def test_invalid_chunk_id_fails_compilation(chunk_ws):
    """Structurally invalid chunk_id (CHUNK_INVALID_CHUNK_ID) must fail fast (no silent skip)."""
    bad_ref = ContextChunkRef(chunk_id="chk-_tier1", tenant_id=None)
    item = _item("t-skip", bad_ref)
    result = compile_plan_item(item, verify_chunks=True)
    assert not result.ok
    assert result.error is not None
    assert result.error.code.value == "COMPILATION_FAILED"


def test_valid_then_invalid_chunk_id_fails(chunk_ws):
    """Valid chunk first, invalid second: invalid must still fail the whole compile."""
    from app.core import chunk_store

    cid = chunk_store.write_chunk("t-mixed", chunk_type="ctx", body={"key": "val"})
    good_ref = ContextChunkRef(chunk_id=cid, tenant_id=None)
    bad_ref = ContextChunkRef(chunk_id="chk-_tier0", tenant_id=None)

    item = _item("t-mixed", good_ref, bad_ref)
    result = compile_plan_item(item, verify_chunks=True)

    assert not result.ok
    assert result.error is not None
    assert result.error.code.value == "COMPILATION_FAILED"


def test_missing_chunk_still_fails(chunk_ws):
    """A structurally valid but non-existent chunk_id still produces CHUNK_NOT_FOUND failure."""
    nonexistent = "chk-" + "e" * 32
    bad_ref = ContextChunkRef(chunk_id=nonexistent, tenant_id=None)
    item = _item("t-missing", bad_ref)
    result = compile_plan_item(item, verify_chunks=True)
    assert not result.ok
    assert result.error is not None
    assert result.error.code.value == "CHUNK_NOT_FOUND"
