"""
Kit 锚点 skill：Planner 省略业务 skill_id 时，在业务校验通过后写入确定性锚点，供编译/派发与 checkpoint 一致。

受 ORCH_PLAN_KIT_ANCHOR_SKILL 控制；与 validate_plan_list_business 中 pick_kit_anchor_skill 语义对齐。
"""

from __future__ import annotations

from typing import Sequence

from app.core.capability_kits import effective_plan_item_kit_id, pick_kit_anchor_skill
from app.plan_feature_flags import orch_plan_kit_anchor_skill_enabled
from app.plan_models import PlanList


def materialize_kit_anchor_plan_list(plan_list: PlanList, available_skill_ids: Sequence[str]) -> PlanList:
    """
    将 skill_id 为空的项在存在有效 kit_id（含 PlanList 继承）时，物化为 Kit∩available 的首个成员。
    未开启开关或无锚点时原样返回。
    """
    if not orch_plan_kit_anchor_skill_enabled():
        return plan_list
    items = list(plan_list.items or [])
    if not items:
        return plan_list
    out_items = []
    changed = False
    for it in items:
        if (it.skill_id or "").strip():
            out_items.append(it)
            continue
        kid = effective_plan_item_kit_id(it, plan_list)
        if not kid:
            out_items.append(it)
            continue
        anchor = pick_kit_anchor_skill(kid, available_skill_ids)
        if not anchor:
            out_items.append(it)
            continue
        md = dict(it.metadata or {})
        if not md.get("planner_skill_delegated_to_kit_anchor"):
            md["planner_skill_delegated_to_kit_anchor"] = True
        out_items.append(it.model_copy(update={"skill_id": anchor, "metadata": md}, deep=True))
        changed = True
    if not changed:
        return plan_list
    return plan_list.model_copy(update={"items": out_items}, deep=True)
