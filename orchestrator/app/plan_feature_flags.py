"""
Plan 模式特性：

- 当前版本强制走 PlanList（plan-v1）决策链路，不再提供 legacy 决策路径开关。
- 状态机在决策点调用 `call_plan_list_decision_engine`，经 `validate_plan_list_business` 通过后写入
  `target_context["_latest_plan_list"]`（随 checkpoint 持久化）；失败则 `PLAN_LIST_BUSINESS_REJECT`。
- `ORCH_PLAN_MODE_ENABLED` 已废弃，保留兼容函数仅返回常量 True。
"""

from __future__ import annotations

import os
from typing import Mapping

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


def orch_plan_mode_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """
    强制启用 Planner→PlanList 新链路。
    兼容旧调用签名；environ 参数仅为向后兼容，忽略其值。
    """
    return True


def orch_legacy_decision_path(environ: Mapping[str, str] | None = None) -> bool:
    """legacy 决策路径已下线，固定返回 False。"""
    return False


def orch_plan_kit_anchor_skill_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """
    允许 PlanItem 在声明 kit_id（或继承 PlanList.kit_id）时省略 skill_id：
    业务校验与物化阶段按 Kit 注册表顺序选用当相可用首个成员作为业务锚点。
    `1`/`true`/`yes`/`on` 为开启；未设置或空为关闭（保持历史「必须填 skill_id」）。
    """
    env = environ if environ is not None else os.environ
    raw = (env.get("ORCH_PLAN_KIT_ANCHOR_SKILL") or "").strip().lower()
    return raw in _TRUE_TOKENS
