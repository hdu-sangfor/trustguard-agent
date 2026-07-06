"""plan_business_validate: invalid_chunk_ref_format violation for hallucinated chunk IDs."""
import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core.plan_business_validate import validate_plan_list_business  # type: ignore[import]
from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem, PlanList  # type: ignore[import]


@pytest.fixture(autouse=True)
def _disable_plan_chunk_store_validate_for_chunk_ref_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_PLAN_VALIDATE_CHUNK_IN_STORE", "false")


def _item_with_refs(*chunk_ids: str, skill_id: str = "nmap") -> PlanItem:
    refs = [ContextChunkRef(chunk_id=cid, tenant_id=None) for cid in chunk_ids]
    return PlanItem(
        plan_id="p1",
        task_id="t1",
        skill_id=skill_id,
        plan_content="x",
        context_chunk_refs=refs,
        constraints=PlanConstraints(target_scope="example.com", timeout_seconds=60),
    )


def _validate(item):
    pl = PlanList(task_id="t1", items=[item])
    return validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap", "nuclei"],
        task_target="example.com",
    )


def _validate_with_known(item, known_chunk_ids):
    pl = PlanList(task_id="t1", items=[item])
    return validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap", "nuclei"],
        task_target="example.com",
        known_chunk_ids=known_chunk_ids,
    )


# ── hallucinated IDs ─────────────────────────────────────────────────────────

def test_reject_chk_tier1():
    """chk-_tier1 → body='_tier1' len=6 < 16 → invalid_chunk_ref_format violation."""
    ok, err = _validate(_item_with_refs("chk-_tier1"))
    assert not ok and err is not None
    violations = err.details.get("violations") or []
    codes = [v.get("code") for v in violations]
    assert "invalid_chunk_ref_format" in codes


def test_reject_chk_tier0():
    ok, err = _validate(_item_with_refs("chk-_tier0"))
    assert not ok
    codes = [v.get("code") for v in (err.details.get("violations") or [])]
    assert "invalid_chunk_ref_format" in codes


def test_reject_chk_target_baseline_15_chars():
    """chk-target-baseline → body='target-baseline' len=15 < 16 → rejected."""
    ok, err = _validate(_item_with_refs("chk-target-baseline"))
    assert not ok
    codes = [v.get("code") for v in (err.details.get("violations") or [])]
    assert "invalid_chunk_ref_format" in codes


def test_reject_no_chk_prefix():
    """Chunk ID without 'chk-' prefix is invalid."""
    ok, err = _validate(_item_with_refs("abc1234567890123456"))
    assert not ok
    codes = [v.get("code") for v in (err.details.get("violations") or [])]
    assert "invalid_chunk_ref_format" in codes


# ── valid IDs ────────────────────────────────────────────────────────────────

def test_accept_valid_chunk_id_32chars():
    """chk- + 32 hex chars is a valid generated chunk ID."""
    valid = "chk-" + "a" * 32
    ok, err = _validate(_item_with_refs(valid))
    assert ok and err is None


def test_accept_valid_chunk_id_16chars_minimum():
    """chk- + exactly 16 alphanumeric chars is valid (minimum)."""
    valid = "chk-" + "b" * 16
    ok, err = _validate(_item_with_refs(valid))
    assert ok and err is None


def test_accept_multiple_valid_refs():
    valid1 = "chk-" + "c" * 32
    valid2 = "chk-" + "d" * 16
    ok, err = _validate(_item_with_refs(valid1, valid2))
    assert ok and err is None


def test_reject_unknown_chunk_ref_when_known_ids_provided():
    known = "chk-" + "a" * 32
    fake = "chk-" + "b" * 32
    ok, err = _validate_with_known(_item_with_refs(fake), [known])
    assert not ok and err is not None
    codes = [v.get("code") for v in (err.details.get("violations") or [])]
    assert "unknown_chunk_ref" in codes


# ── error message content ────────────────────────────────────────────────────

def test_violation_message_mentions_tier_keys():
    """Rejection message must guide LLM away from tier structure keys."""
    ok, err = _validate(_item_with_refs("chk-_tier1"))
    assert not ok
    violations = err.details.get("violations") or []
    inv = next(v for v in violations if v.get("code") == "invalid_chunk_ref_format")
    msg = inv.get("message") or ""
    assert "_tier0" in msg or "_tier1" in msg  # Anti-hallucination guidance in message
