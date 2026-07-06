"""
R5c：经验库「特征抽取」写入策略（契约级）。

- 仅当 tools_registry 中 `extract_kb_features: true` 时，视为该 skill **声明**实现包内特征抽取，
  允许后续 KB 管线走「结构化特征 → 向量」等完整路径（与 kb-r1c 落地代码配合）。
- 缺省或 false：视为 **no-op 契约** — 不得依赖 `extract_kb_features` 产物做向量写入；
  仅允许意图句 + chunk 指针等注册表/文档标明的降级路径（由调用方显式分支）。
"""
from __future__ import annotations

from app.tools_registry import get_tool_info


def extract_kb_features_declared(skill_id: str) -> bool:
    """注册表是否声明该 skill 实现 extract_kb_features（严格：仅 YAML 布尔 true）。"""
    info = get_tool_info(skill_id)
    if not info:
        return False
    return info.get("extract_kb_features") is True
