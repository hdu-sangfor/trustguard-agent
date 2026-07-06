"""单测：Kit 锚点 skill 物化（ORCH_PLAN_KIT_ANCHOR_SKILL）。"""

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core.plan_kit_anchor import materialize_kit_anchor_plan_list  # noqa: E402
from app.plan_models import PlanConstraints, PlanItem, PlanList  # noqa: E402


def _item(*, skill_id: str, kit_id: str | None = None) -> PlanItem:
    return PlanItem(
        plan_id="p1",
        task_id="t1",
        skill_id=skill_id,
        plan_content="x",
        context_chunk_refs=[],
        constraints=PlanConstraints(target_scope="host:example.com", timeout_seconds=60),
        kit_id=kit_id,
    )


def test_materialize_noop_when_flag_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ORCH_PLAN_KIT_ANCHOR_SKILL", raising=False)
    pl = PlanList(
        task_id="t1",
        kit_id="web-recon-v1",
        items=[_item(skill_id="")],
    )
    out = materialize_kit_anchor_plan_list(pl, ["httpx", "katana"])
    assert out is pl
    assert (out.items[0].skill_id or "").strip() == ""


def test_materialize_fills_skill_id_when_flag_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCH_PLAN_KIT_ANCHOR_SKILL", "1")
    pl = PlanList(
        task_id="t1",
        kit_id="web-recon-v1",
        items=[_item(skill_id="")],
    )
    out = materialize_kit_anchor_plan_list(pl, ["httpx", "katana"])
    assert out.items[0].skill_id == "katana"
    assert (out.items[0].metadata or {}).get("planner_skill_delegated_to_kit_anchor") is True
