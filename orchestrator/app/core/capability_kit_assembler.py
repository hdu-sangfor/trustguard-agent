"""
CapabilityKitAssembler：从仓库 skills/<id>/SKILL.md 抽取短摘要，拼入战术前缀（plan_content）。

受 ORCH_KIT_MANUAL_* 环境变量约束；默认直连 skill 容器。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_MANUAL_CACHE: dict[str, str] = {}


def _repo_root() -> Path:
    raw = (os.getenv("TRUSTGUARD_REPO_ROOT") or "").strip()
    if raw:
        return Path(raw)
    # orchestrator/app/core/ -> parents[3] = repository root.
    return Path(__file__).resolve().parents[3]


def _skills_dir() -> Path:
    return _repo_root() / "skills"


def _strip_yaml_frontmatter(text: str) -> str:
    t = (text or "").lstrip("\ufeff")
    if not t.startswith("---"):
        return t.strip()
    parts = t.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return t.strip()


def _read_skill_md_excerpt(skill_id: str, *, per_skill_max_chars: int, max_lines: int) -> str:
    sid = (skill_id or "").strip()
    if not sid:
        return ""
    path = _skills_dir() / sid / "SKILL.md"
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("kit_manual: read failed %s: %s", path, exc)
        return ""
    body = _strip_yaml_frontmatter(raw)
    lines = [ln.rstrip() for ln in body.splitlines() if ln.strip()][:max(1, max_lines)]
    chunk = "\n".join(lines).strip()
    if len(chunk) > per_skill_max_chars:
        chunk = chunk[: max(0, per_skill_max_chars - 1)].rstrip() + "…"
    return chunk


def kit_manual_enabled() -> bool:
    v = (os.getenv("ORCH_KIT_MANUAL_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def kit_manual_max_total_chars() -> int:
    raw = (os.getenv("ORCH_KIT_MANUAL_MAX_TOTAL_CHARS") or "").strip()
    if not raw:
        return 4096
    try:
        return max(512, min(int(raw), 32000))
    except ValueError:
        return 4096


def kit_manual_per_skill_chars() -> int:
    raw = (os.getenv("ORCH_KIT_MANUAL_PER_SKILL_CHARS") or "").strip()
    if not raw:
        return 900
    try:
        return max(120, min(int(raw), 4000))
    except ValueError:
        return 900


def kit_manual_max_lines_per_skill() -> int:
    raw = (os.getenv("ORCH_KIT_MANUAL_MAX_LINES") or "").strip()
    if not raw:
        return 28
    try:
        return max(5, min(int(raw), 120))
    except ValueError:
        return 28


def clear_kit_manual_cache_for_tests() -> None:
    _MANUAL_CACHE.clear()


def build_kit_manual_block(kit_id: str, ordered_skill_ids: List[str]) -> str:
    """
    按 Kit 内顺序（通常为 narrow 后的 pool）拼接各 SKILL.md 摘录；总长度受 ORCH_KIT_MANUAL_MAX_TOTAL_CHARS 限制。
    """
    if not kit_manual_enabled():
        return ""
    kid = (kit_id or "").strip()
    if not kid or not ordered_skill_ids:
        return ""
    cache_key = f"{kid}|{','.join(ordered_skill_ids)}|{kit_manual_max_total_chars()}|{kit_manual_per_skill_chars()}"
    if cache_key in _MANUAL_CACHE:
        return _MANUAL_CACHE[cache_key]

    total_budget = kit_manual_max_total_chars()
    per_skill = kit_manual_per_skill_chars()
    max_lines = kit_manual_max_lines_per_skill()
    parts: list[str] = []
    used = 0
    for sid in ordered_skill_ids:
        ex = _read_skill_md_excerpt(sid, per_skill_max_chars=per_skill, max_lines=max_lines)
        if not ex:
            continue
        header = f"### {sid}\n"
        block = header + ex
        if used + len(block) + 2 > total_budget:
            room = total_budget - used - len(header) - 4
            if room < 80:
                break
            ex = ex[:room].rstrip() + "…"
            block = header + ex
        parts.append(block.strip())
        used += len(block) + 2
        if used >= total_budget:
            break
    out = "\n\n".join(parts).strip()
    _MANUAL_CACHE[cache_key] = out
    return out
