"""
R5c：编排侧经验库特征抽取策略（与 Executor 共用 tools_registry 语义）。

默认未声明 = false：禁止走「依赖 extract_kb_features 的向量特征写入」路径，
除非调用方显式实现降级（如仅 chunk_id / 意图句）。
"""
from __future__ import annotations

from app.core.skill_pack_loader import get_tool_registry_entry


class KbFeaturePolicyError(Exception):
    __slots__ = ("code", "message")

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def extract_kb_features_declared(skill_id: str) -> bool:
    ent = get_tool_registry_entry(skill_id)
    if not ent:
        return False
    return ent.get("extract_kb_features") is True


def assert_vector_feature_write_allowed(skill_id: str) -> None:
    """
    在写入「依赖 skill 侧结构化特征抽取」的向量前调用；未声明则抛错（供 KB 写入门禁）。
    """
    if extract_kb_features_declared(skill_id):
        return
    raise KbFeaturePolicyError(
        "KB_FEATURE_EXTRACT_NOT_DECLARED",
        f"skill_id={skill_id!r} has no extract_kb_features: true in tools_registry; "
        "vector feature write path is forbidden by R5c contract",
    )
