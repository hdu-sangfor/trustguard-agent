"""
按 skill_id 从 skills/<skill_id>/ 加载 SKILL.md、_meta.json，并附带 tools_registry 条目（R5a）。

环境变量：
- SKILLS_ROOT：技能包根目录，默认仓库根下 skills/
- TOOLS_REGISTRY_YAML：与 Executor 相同，默认仓库根 docker/tools_registry.yaml（R5b 契约见 registry_validation；
  R5c 可选 `extract_kb_features: bool`，默认 false，见 core/kb_feature_policy.py）
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from app.skill_pack_models import SkillPack

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SKILLS_ROOT = _PROJECT_ROOT / "skills"
_DEFAULT_REGISTRY = _PROJECT_ROOT / "docker" / "tools_registry.yaml"


class SkillPackLoadError(Exception):
    __slots__ = ("code", "message", "http_status", "details")

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 404,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        super().__init__(message)


def skill_pack_error_http_detail(err: SkillPackLoadError) -> dict[str, Any]:
    return {
        "structured_error": {
            "kind": "skill_pack",
            "code": err.code,
            "message": err.message,
            "details": err.details,
        }
    }


def _skills_root() -> Path:
    raw = (os.getenv("SKILLS_ROOT") or "").strip()
    if raw:
        return Path(raw).resolve()
    return _DEFAULT_SKILLS_ROOT.resolve()


def _registry_path() -> Path:
    p = (os.getenv("TOOLS_REGISTRY_YAML") or "").strip()
    return Path(p).resolve() if p else _DEFAULT_REGISTRY.resolve()


def _load_registry_tools() -> dict[str, Any]:
    path = _registry_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        tools_root = (
            os.getenv("TRUSTGUARD_TOOLS_ROOT")
            or str(_PROJECT_ROOT / "TRUSTGUARD_TOOLS_ROOT")
        ).replace(
            "\\", "/"
        )
        raw = re.sub(r"\$\{TRUSTGUARD_TOOLS_ROOT\}", tools_root, raw)
        data = yaml.safe_load(raw) or {}
        tools = data.get("tools") or {}
        return tools if isinstance(tools, dict) else {}
    except Exception:
        return {}


def get_tool_registry_entry(skill_id: str) -> dict[str, Any] | None:
    """返回 tools_registry.yaml 中 `tools[skill_id]` 的拷贝；不存在或非 dict 则 None。"""
    tools = _load_registry_tools()
    sid = (skill_id or "").strip()
    if not sid:
        return None
    ent = tools.get(sid)
    return dict(ent) if isinstance(ent, dict) else None


def _validate_skill_id(skill_id: str) -> str:
    sid = (skill_id or "").strip()
    if not sid or len(sid) > 128:
        raise SkillPackLoadError("SKILL_PACK_INVALID_ID", "invalid skill_id", http_status=400)
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", sid, re.I):
        raise SkillPackLoadError(
            "SKILL_PACK_INVALID_ID",
            "skill_id must match [a-z0-9][a-z0-9_-]*",
            http_status=400,
        )
    if ".." in sid or "/" in sid or "\\" in sid:
        raise SkillPackLoadError("SKILL_PACK_INVALID_ID", "skill_id traversal", http_status=400)
    return sid


def _safe_pack_dir(skill_id: str) -> Path:
    root = _skills_root()
    sid = _validate_skill_id(skill_id)
    d = (root / sid).resolve()
    try:
        d.relative_to(root.resolve())
    except ValueError as exc:
        raise SkillPackLoadError(
            "SKILL_PACK_PATH_ESCAPE",
            "skill pack path escapes SKILLS_ROOT",
            http_status=400,
        ) from exc
    return d


def _split_skill_md_front_matter(raw: str) -> tuple[dict[str, Any] | None, str]:
    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, text
    fm_block = "\n".join(lines[1:end_idx])
    try:
        loaded = yaml.safe_load(fm_block)
    except Exception:
        return None, text
    if not isinstance(loaded, dict):
        return None, text
    body = "\n".join(lines[end_idx + 1 :])
    return loaded, body.lstrip("\n")


def load_skill_pack(skill_id: str) -> SkillPack:
    """
    解析技能包。要求存在 SKILLS_ROOT/<skill_id>/ 目录与 SKILL.md；
    _meta.json 可选；注册表条目可选（无则 registry_entry=null）。

    internal_agent_plane：无磁盘技能包（执行器 app/agent_plane），仅返回注册表元数据供 Compiler/观测。
    """
    sid = _validate_skill_id(skill_id)
    reg = get_tool_registry_entry(sid)
    if isinstance(reg, dict) and reg.get("internal_agent_plane") is True:
        return SkillPack(
            skill_id=sid,
            pack_dir="",
            skill_md_path=None,
            skill_md_raw=None,
            skill_front_matter={
                "name": sid,
                "description": "internal agent control plane; execution in executor app/agent_plane",
            },
            skill_md_body=None,
            meta_path=None,
            meta={},
            registry_entry=dict(reg),
        )

    d = _safe_pack_dir(skill_id)

    if not d.is_dir():
        raise SkillPackLoadError(
            "SKILL_PACK_UNKNOWN_SKILL",
            f"skill pack directory not found: {d}",
            http_status=404,
        )

    skill_md = d / "SKILL.md"
    if not skill_md.is_file():
        raise SkillPackLoadError(
            "SKILL_PACK_MISSING_SKILL_MD",
            "SKILL.md missing under skill pack",
            http_status=404,
            details={"expected_path": str(skill_md.resolve())},
        )

    try:
        raw_md = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillPackLoadError(
            "SKILL_PACK_READ_ERROR",
            f"failed to read SKILL.md: {exc}",
            http_status=500,
        ) from exc

    fm, body = _split_skill_md_front_matter(raw_md)

    meta_path = d / "_meta.json"
    meta: dict[str, Any] = {}
    mp: str | None = None
    if meta_path.is_file():
        mp = str(meta_path.resolve())
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                meta = {}
        except (json.JSONDecodeError, OSError):
            meta = {}

    tools = _load_registry_tools()
    reg = tools.get(sid)
    registry_entry = dict(reg) if isinstance(reg, dict) else None

    return SkillPack(
        skill_id=sid,
        pack_dir=str(d.resolve()),
        skill_md_path=str(skill_md.resolve()),
        skill_md_raw=raw_md,
        skill_front_matter=fm,
        skill_md_body=body if body else None,
        meta_path=mp,
        meta=meta,
        registry_entry=registry_entry,
    )
