from __future__ import annotations

from typing import Protocol

from app.models import SkillRequest, SkillResult


class Skill(Protocol):
    id: str
    category: str

    def execute(self, req: SkillRequest) -> SkillResult:
        ...
