"""
Capability Kit（能力包）注册：用于 Plan 模式下约束可用技能并注入战术前缀。

可选 JSON 覆盖见 ORCH_CAPABILITY_KITS_PATH。
Kit 可声明 allowed_phases（Phase 枚举 value 列表），由 validate_plan_list_business 在传入 current_phase 时门禁。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.enums import Phase
from app.plan_models import PlanItem, PlanList

logger = logging.getLogger(__name__)

_VALID_PHASE_VALUES = frozenset(p.value for p in Phase)

# 内置最小集合；值为 { "tools": [...], "allowed_phases": [...] } 或兼容旧式仅列表（在 _builtin_entries）
_BUILTIN_SPECS: Dict[str, Any] = {
    "web-recon-v1": {
        "tools": [
            "katana",
            "dirsearch",
            "httpx",
            "nuclei",
            "whatweb-fingerprint",
            "curl-raw",
            "http-enum",
            # 新增：task-c90db15c... (thinkphp 5.0.23) 暴露的问题 ——
            # LLM 在 RECON 阶段给 PlanList item 声明 kit_id=web-recon-v1，
            # 但指纹 (ehole)、工件复盘 (read_workspace_artifact) 被踢出 kit，
            # 导致 5 次 PLAN_LIST_BUSINESS_REJECT / skill_not_in_capability_kit。
            # ehole 是 recon 阶段必备的指纹识别工具；read_workspace_artifact 是通用复盘工具。
            "ehole",
            "read_workspace_artifact",
        ],
        # 未声明 allowed_phases：任意阶段可用（与历史行为一致）
    },
}

_registry_pair: Optional[Tuple[Dict[str, List[str]], Dict[str, frozenset[str]]]] = None
_registry_file_mtime: Optional[float] = None  # st_mtime of ORCH_CAPABILITY_KITS_PATH at last load


def _parse_allowed_phases(raw: Any) -> frozenset[str] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[str] = []
    for p in raw:
        s = str(p).strip().upper()
        if s in _VALID_PHASE_VALUES:
            out.append(s)
    return frozenset(out) if out else None


def _parse_kit_spec(spec: Any) -> tuple[List[str], frozenset[str] | None]:
    if isinstance(spec, list):
        tools = [str(x).strip() for x in spec if str(x).strip()]
        return tools, None
    if isinstance(spec, dict):
        raw_tools = spec.get("tools") or spec.get("members") or spec.get("skill_ids")
        tools: List[str] = []
        if isinstance(raw_tools, list):
            tools = [str(x).strip() for x in raw_tools if str(x).strip()]
        phases = _parse_allowed_phases(spec.get("allowed_phases") or spec.get("phases"))
        return tools, phases
    return [], None


def _deep_merge_specs(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for kid, spec in (extra or {}).items():
        key = str(kid or "").strip()
        if not key:
            continue
        out[key] = spec
    return out


def _current_kit_file_mtime() -> Optional[float]:
    """ORCH_CAPABILITY_KITS_PATH 的当前 st_mtime；文件不存在或无配置时返回 None。"""
    path = (os.getenv("ORCH_CAPABILITY_KITS_PATH") or "").strip()
    if not path:
        return None
    try:
        p = Path(path)
        if p.is_file():
            return p.stat().st_mtime
    except OSError:
        pass
    return None


def _load_registry_pair() -> Tuple[Dict[str, List[str]], Dict[str, frozenset[str]]]:
    global _registry_pair, _registry_file_mtime

    # mtime 检查：文件变更时自动失效缓存（v1-a249 hotreload）
    current_mtime = _current_kit_file_mtime()
    if _registry_pair is not None and current_mtime == _registry_file_mtime:
        return _registry_pair
    if _registry_pair is not None and current_mtime != _registry_file_mtime:
        logger.info(
            "capability_kits: file mtime changed (%s -> %s), reloading registry",
            _registry_file_mtime, current_mtime,
        )

    merged_specs: Dict[str, Any] = dict(_BUILTIN_SPECS)
    path = (os.getenv("ORCH_CAPABILITY_KITS_PATH") or "").strip()
    if path:
        p = Path(path)
        if p.is_file():
            try:
                raw = p.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    merged_specs = _deep_merge_specs(merged_specs, data)
                else:
                    logger.warning("capability_kits: JSON root must be object, got %s", type(data).__name__)
            except Exception as exc:
                logger.warning("capability_kits: failed to load %s: %s", path, exc)
        else:
            logger.warning("capability_kits: ORCH_CAPABILITY_KITS_PATH not a file: %s", path)

    tools_map: Dict[str, List[str]] = {}
    phases_map: Dict[str, frozenset[str]] = {}
    for kid, spec in merged_specs.items():
        tools, phases = _parse_kit_spec(spec)
        if tools:
            tools_map[kid] = tools
        if phases is not None:
            phases_map[kid] = phases

    _registry_pair = (tools_map, phases_map)
    _registry_file_mtime = current_mtime
    return _registry_pair


def reload_kit_registry_for_tests() -> None:
    """单测用：清空缓存以便覆盖环境变量。"""
    global _registry_pair, _registry_file_mtime
    _registry_pair = None
    _registry_file_mtime = None


def load_kit_registry() -> Dict[str, List[str]]:
    """返回 kit_id -> 成员工具 id 列表（去重保序由调用方决定）。"""
    return _load_registry_pair()[0]


def get_kit_phase_allowlist(kit_id: str) -> frozenset[str] | None:
    """
    若 Kit 声明了 allowed_phases，返回允许的 Phase.value 集合；否则 None 表示不做阶段门禁。
    未知 kit_id 返回 None（业务层应先按 get_kit_member_tools 判存）。
    """
    kid = (kit_id or "").strip()
    if not kid:
        return None
    _, phases_map = _load_registry_pair()
    return phases_map.get(kid)


def get_kit_member_tools(kit_id: str) -> Optional[List[str]]:
    """未知 kit 返回 None。"""
    kid = (kit_id or "").strip()
    if not kid:
        return None
    reg = load_kit_registry()
    tools = reg.get(kid)
    if not tools:
        return None
    return list(tools)


def pick_kit_anchor_skill(kit_id: str, available_skill_ids: Sequence[str]) -> str | None:
    """
    Kit-only / 省略 skill_id 时：按 Kit 定义顺序选取第一个同时出现在 available_skill_ids 中的成员。
    无交集时返回 None。
    """
    members = get_kit_member_tools(kit_id)
    if not members:
        return None
    allow = {str(s).strip() for s in (available_skill_ids or []) if str(s).strip()}
    for m in members:
        if m in allow:
            return m
    return None


def plan_item_kit_id(item: PlanItem) -> str:
    v = (getattr(item, "kit_id", None) or "").strip()
    if v:
        return v
    md = item.metadata or {}
    raw = md.get("kit_id") if md.get("kit_id") is not None else md.get("capability_kit_id")
    return str(raw or "").strip()


def plan_item_tactical_goal(item: PlanItem) -> str:
    v = (getattr(item, "tactical_goal", None) or "").strip()
    if v:
        return v
    md = item.metadata or {}
    raw = md.get("tactical_goal")
    return str(raw or "").strip()


def effective_plan_item_kit_id(item: PlanItem, plan_list: PlanList | None) -> str:
    """Item / metadata 优先，否则继承 PlanList.kit_id。"""
    k = plan_item_kit_id(item)
    if k:
        return k
    if plan_list is None:
        return ""
    return (getattr(plan_list, "kit_id", None) or "").strip()


def effective_plan_item_tactical_goal(item: PlanItem, plan_list: PlanList | None) -> str:
    """Item / metadata 优先，否则继承 PlanList.tactical_goal。"""
    t = plan_item_tactical_goal(item)
    if t:
        return t
    if plan_list is None:
        return ""
    return (getattr(plan_list, "tactical_goal", None) or "").strip()


def narrow_available_skills_for_kit(
    available_skills: List[str],
    item: PlanItem,
    plan_list: PlanList | None = None,
) -> List[str]:
    """
    若 PlanItem（或 PlanList 缺省）声明 kit_id，则将会 phase 可用列表收窄为「Kit 定义 ∩ 当前可用」；
    无 kit_id 时返回原列表副本。
    """
    kid = effective_plan_item_kit_id(item, plan_list)
    if not kid:
        return list(available_skills or [])
    members = get_kit_member_tools(kid)
    if not members:
        return list(available_skills or [])
    allow = {str(s).strip() for s in available_skills or [] if str(s).strip()}
    return [m for m in members if m in allow]
