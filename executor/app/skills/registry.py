from __future__ import annotations

from typing import Dict

from app.skills.base import Skill
from app.skills.dynamic_loader import load_skills
from app.skills.external_script_skill import ExternalScriptSkill
from app.tools_registry import list_skills_with_category


def _category_map() -> dict[str, str]:
    return {x["skill_id"]: (x.get("category") or "Unknown") for x in list_skills_with_category()}


def _build_skills() -> Dict[str, Skill]:
    categories = _category_map()
    loaded = load_skills()
    skills: Dict[str, Skill] = {}
    for skill_id, item in loaded.items():
        if item.script_path is None:
            continue
        skills[skill_id] = ExternalScriptSkill(
            skill_id=skill_id,
            category=categories.get(skill_id, "Unknown"),
            script_path=item.script_path,
        )
    return skills


_SKILLS: Dict[str, Skill] = _build_skills()


def refresh_skills() -> list[str]:
    global _SKILLS
    _SKILLS = _build_skills()
    return sorted(_SKILLS.keys())


def get_skill(skill_id: str) -> Skill | None:
    return _SKILLS.get(skill_id)


def list_skill_ids() -> list[str]:
    return sorted(_SKILLS.keys())
