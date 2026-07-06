"""按 skill_id 注册的无 LLM 编译规则（R4b+）。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.plan_models import PlanItem, ResolvedContextChunk

from .nmap import build_nmap_params
from .passthrough import build_passthrough_params, get_passthrough_skill_ids

# 技能专属 builder 优先注册；其余常用技能统一走白名单 passthrough，
# 避免 LLM 在 PlanItem.metadata.params 中指定的 artifact_ref/run_id/framework_hint 等
# 关键字段被 CompiledInstruction.params 无声丢弃（已在 task-0385... 日志中观察到）。
SKILL_PARAM_BUILDERS: dict[str, Callable[[PlanItem], dict[str, Any]]] = {
    "nmap": build_nmap_params,
}
for _sid in get_passthrough_skill_ids():
    SKILL_PARAM_BUILDERS.setdefault(_sid, build_passthrough_params)


def build_params_for_skill(plan_item: PlanItem) -> dict[str, Any]:
    sid = (plan_item.skill_id or "").strip().lower()
    fn = SKILL_PARAM_BUILDERS.get(sid)
    if fn is None:
        return {}
    return fn(plan_item)


def merge_resolved_context_into_skill_params(
    skill_id: str,
    params: dict[str, Any],
    resolved_chunks: list[ResolvedContextChunk],
) -> dict[str, Any]:
    """编译后钩子；当前普通技能不把 resolved chunks 注入执行参数。"""
    _ = (skill_id, resolved_chunks)
    return params
