"""Compiler 输入：按 skill_id 解析后的 Skill 包（SKILL.md + _meta.json + 注册表条目）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillPack(BaseModel):
    """供 InstructionCompiler 消费的稳定结构（可 JSON 序列化）。"""

    skill_id: str = Field(..., description="目录名 / 注册表键")
    pack_dir: str = Field(..., description="技能包根目录绝对路径")
    skill_md_path: str | None = Field(None, description="SKILL.md 绝对路径")
    skill_md_raw: str | None = Field(None, description="SKILL.md 全文")
    skill_front_matter: dict[str, Any] | None = Field(
        None, description="SKILL.md 顶部 YAML front matter（若可解析）"
    )
    skill_md_body: str | None = Field(None, description="去掉 front matter 后的正文")
    meta_path: str | None = Field(None, description="_meta.json 绝对路径")
    meta: dict[str, Any] = Field(default_factory=dict, description="_meta.json 内容，缺省为 {}")
    registry_entry: dict[str, Any] | None = Field(
        None, description="docker/tools_registry.yaml 中对应 tools[skill_id] 的原始条目（无则 null）"
    )
