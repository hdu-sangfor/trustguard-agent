from pathlib import Path

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core import capability_kits  # type: ignore[import]
from app.core.plan_business_validate import validate_plan_list_business  # type: ignore[import]
from app.enums import Phase  # type: ignore[import]
from app.plan_models import ContextChunkRef, PlanConstraints, PlanItem, PlanList  # type: ignore[import]


@pytest.fixture(autouse=True)
def _disable_plan_chunk_store_validate_for_most_unit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """本模块多数用例不落地 chunk；关闭 workspace read_chunk 强校验。"""
    monkeypatch.setenv("ORCH_PLAN_VALIDATE_CHUNK_IN_STORE", "false")


@pytest.fixture
def chunk_workspace_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """少数用例：临时 workspace + 开启与线上一致的 chunk 落盘校验。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("ORCH_PLAN_VALIDATE_CHUNK_IN_STORE", "true")
    return tmp_path


def _item(
    *,
    task_id: str = "t1",
    plan_id: str = "p1",
    skill_id: str = "nmap",
    target_scope: str = "host:example.com",
    refs: list | None = None,
    kit_id: str | None = None,
    tactical_goal: str | None = None,
) -> PlanItem:
    return PlanItem(
        plan_id=plan_id,
        task_id=task_id,
        skill_id=skill_id,
        plan_content="x",
        context_chunk_refs=refs or [],
        constraints=PlanConstraints(target_scope=target_scope, timeout_seconds=60),
        kit_id=kit_id,
        tactical_goal=tactical_goal,
    )


def test_business_ok_empty_items():
    pl = PlanList(task_id="t1", items=[])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
    )
    assert ok and err is None


def test_reject_unknown_capability_kit():
    pl = PlanList(task_id="t1", items=[_item(skill_id="nmap", kit_id="no-such-kit-ever")])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap", "httpx"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert any(v.get("code") == "unknown_capability_kit" for v in (err.details.get("violations") or []))


def test_reject_skill_not_in_capability_kit():
    pl = PlanList(task_id="t1", items=[_item(skill_id="nmap", kit_id="web-recon-v1")])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap", "httpx", "katana"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert any(v.get("code") == "skill_not_in_capability_kit" for v in (err.details.get("violations") or []))


def test_top_level_kit_id_inherited_for_kit_member_validation():
    """plan-v1：根 kit_id 继承到未声明 kit 的 item，仍须满足 kit 成员校验。"""
    pl = PlanList(
        task_id="t1",
        kit_id="web-recon-v1",
        items=[_item(skill_id="httpx", kit_id=None)],
    )
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["httpx", "katana"],
        task_target="https://example.com",
    )
    assert ok and err is None


def test_reject_empty_skill_id_when_kit_anchor_off():
    pl = PlanList(
        task_id="t1",
        kit_id="web-recon-v1",
        items=[_item(skill_id="", kit_id=None)],
    )
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["httpx", "katana"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert any(v.get("code") == "missing_planner_skill_id" for v in (err.details.get("violations") or []))


def test_ok_empty_skill_when_kit_anchor_on_and_kit_resolves(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ORCH_PLAN_KIT_ANCHOR_SKILL", "1")
    pl = PlanList(
        task_id="t1",
        kit_id="web-recon-v1",
        items=[_item(skill_id="", kit_id=None)],
    )
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["httpx", "katana"],
        task_target="https://example.com",
    )
    assert ok and err is None


def test_kit_anchor_no_available_member(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCH_PLAN_KIT_ANCHOR_SKILL", "1")
    pl = PlanList(
        task_id="t1",
        kit_id="web-recon-v1",
        items=[_item(skill_id="", kit_id=None)],
    )
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert any(v.get("code") == "kit_anchor_no_available_member" for v in (err.details.get("violations") or []))


def test_top_level_kit_id_inherited_rejects_skill_not_in_kit():
    pl = PlanList(
        task_id="t1",
        kit_id="web-recon-v1",
        items=[_item(skill_id="nmap", kit_id=None)],
    )
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap", "httpx"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert any(v.get("code") == "skill_not_in_capability_kit" for v in (err.details.get("violations") or []))


def test_capability_kit_phase_not_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    kits = {
        "phase-locked-kit": {
            "tools": ["httpx"],
            "allowed_phases": ["RECON"],
        }
    }
    p = tmp_path / "kits.json"
    p.write_text(__import__("json").dumps(kits), encoding="utf-8")
    monkeypatch.setenv("ORCH_CAPABILITY_KITS_PATH", str(p))

    capability_kits.reload_kit_registry_for_tests()
    try:
        pl = PlanList(
            task_id="t1",
            items=[_item(skill_id="httpx", kit_id="phase-locked-kit")],
        )
        capability_kits.reload_kit_registry_for_tests()
        ok, err = validate_plan_list_business(
            pl,
            expected_task_id="t1",
            available_skill_ids=["httpx"],
            task_target="https://example.com",
            current_phase=Phase.VULN_SCAN,
        )
        assert not ok and err is not None
        assert any(
            v.get("code") == "capability_kit_phase_not_allowed" for v in (err.details.get("violations") or [])
        )
    finally:
        capability_kits.reload_kit_registry_for_tests()


def test_kit_id_ok_when_skill_in_kit():
    pl = PlanList(task_id="t1", items=[_item(skill_id="httpx", kit_id="web-recon-v1", tactical_goal="map surface")])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["httpx", "katana", "dirsearch"],
        task_target="https://example.com",
    )
    assert ok and err is None


def test_reject_unknown_skill():
    pl = PlanList(task_id="t1", items=[_item(skill_id="not-a-real-skill")])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert err.code.value == "INVALID_PLAN_LIST"
    codes = {v["code"] for v in (err.details.get("violations") or [])}
    assert "unknown_skill" in codes


def test_reject_target_scope_mismatch():
    pl = PlanList(task_id="t1", items=[_item(target_scope="host:evil.test")])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert any(v.get("code") == "target_scope_mismatch" for v in (err.details.get("violations") or []))


def test_reject_chunk_refs_over_cap():
    refs = [ContextChunkRef(chunk_id=f"c{i}") for i in range(3)]
    pl = PlanList(task_id="t1", items=[_item(refs=refs)])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
        max_chunk_refs_per_item=2,
    )
    assert not ok and err is not None
    assert any(v.get("code") == "chunk_refs_exceeded" for v in (err.details.get("violations") or []))


def test_skill_alias_resolves(monkeypatch: pytest.MonkeyPatch):
    pl = PlanList(task_id="t1", items=[_item(skill_id="web-fingerprint")])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["whatweb-fingerprint"],
        task_target="https://example.com",
        skill_aliases={"web-fingerprint": "whatweb-fingerprint"},
    )
    assert ok and err is None


def test_task_id_mismatch():
    pl = PlanList(task_id="other", items=[])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
    )
    assert not ok and err is not None
    assert any(v.get("code") == "task_id_mismatch" for v in (err.details.get("violations") or []))


def test_reject_execution_kind_conflict_when_phase_provided() -> None:
    it = _item(skill_id="nmap", plan_id="p-ek")
    it.metadata["execution_kind"] = "agent"
    pl = PlanList(task_id="t1", items=[it])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
        current_phase=Phase.RECON,
    )
    assert not ok and err is not None
    assert any(v.get("code") == "execution_kind_conflict" for v in (err.details.get("violations") or []))


def test_no_execution_kind_check_when_phase_omitted() -> None:
    """不传 current_phase 时不做 execution_kind 合并校验（兼容旧调用方）。"""
    it = _item(skill_id="nmap", plan_id="p-ek2")
    it.metadata["execution_kind"] = "agent"
    pl = PlanList(task_id="t1", items=[it])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
    )
    assert ok and err is None


def test_allow_direct_skill() -> None:
    pl = PlanList(
        task_id="t1",
        items=[_item(skill_id="nmap", plan_id="p-direct")],
    )
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap", "httpx"],
        task_target="https://example.com",
    )
    assert ok and err is None


def test_strict_chunk_store_accepts_workspace_chunk(chunk_workspace_strict: Path) -> None:
    from app.core import chunk_store

    cid = chunk_store.write_chunk("t1", chunk_type="ctx", body={"ok": True})
    ref = ContextChunkRef(chunk_id=cid, tenant_id=None)
    pl = PlanList(task_id="t1", items=[_item(refs=[ref])])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
        known_chunk_ids=[cid],
    )
    assert ok and err is None


def test_strict_chunk_store_rejects_missing_even_if_in_known_list(chunk_workspace_strict: Path) -> None:
    missing = "chk-" + "f" * 32
    ref = ContextChunkRef(chunk_id=missing, tenant_id=None)
    pl = PlanList(task_id="t1", items=[_item(refs=[ref])])
    ok, err = validate_plan_list_business(
        pl,
        expected_task_id="t1",
        available_skill_ids=["nmap"],
        task_target="https://example.com",
        known_chunk_ids=[missing],
    )
    assert not ok and err is not None
    codes = [v.get("code") for v in (err.details.get("violations") or [])]
    assert "chunk_not_found_in_workspace" in codes
