from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import shutil

import yaml


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SKILLS_ROOT = _PROJECT_ROOT / "skills"


def _has_required_bin(bin_name: str) -> bool:
    """
    兼容 Windows 本地模式：
    - baidu-search 常声明依赖 python3，但 Windows 常只有 python/py。
    - 非容器模式下允许 python3 -> (python|py) 回退，避免误判为“技能不存在”。
    """
    b = (bin_name or "").strip()
    if not b:
        return True
    if shutil.which(b):
        return True
    if b.lower() == "python3":
        return shutil.which("python") is not None or shutil.which("py") is not None
    return False


@dataclass(frozen=True)
class LoadedSkill:
    skill_id: str
    skill_dir: Path
    script_path: Path | None
    description: str
    metadata: dict[str, Any]


def get_skills_root() -> Path:
    root = os.getenv("TRUSTGUARD_SKILLS_ROOT")
    if root:
        return Path(root).resolve()
    return _DEFAULT_SKILLS_ROOT


def _parse_skill_markdown(path: Path) -> tuple[str, str, dict[str, Any]]:
    if not path.exists():
        return path.parent.name, "", {}
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return path.parent.name, "", {}
    chunks = raw.split("---", 2)
    if len(chunks) < 3:
        return path.parent.name, "", {}
    try:
        data = yaml.safe_load(chunks[1]) or {}
    except Exception:
        data = {}
    skill_id = str(data.get("name") or path.parent.name).strip() or path.parent.name
    description = str(data.get("description") or "").strip()
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return skill_id, description, metadata


def _detect_script_path(skill_dir: Path) -> Path | None:
    meta_file = skill_dir / "_meta.json"
    run_py = skill_dir / "run.py"
    if run_py.exists():
        return run_py
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                entrypoint = meta.get("entrypoint")
                if isinstance(entrypoint, str) and entrypoint.strip():
                    candidate = skill_dir / entrypoint.strip()
                    if candidate.exists():
                        return candidate
        except Exception:
            pass
    for candidate in (
        skill_dir / "scripts" / "execute.py",
        skill_dir / "scripts" / "main.py",
        skill_dir / "scripts" / "search.py",
    ):
        if candidate.exists():
            return candidate
    return None


def load_skills() -> dict[str, LoadedSkill]:
    root = get_skills_root()
    if not root.exists():
        return {}
    loaded: dict[str, LoadedSkill] = {}
    use_skill_containers = os.getenv("EXECUTOR_USE_SKILL_CONTAINERS", "true").lower() == "true"
    for skill_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        skill_id, description, metadata = _parse_skill_markdown(skill_dir / "SKILL.md")
        script_path = _detect_script_path(skill_dir)
        runtime_meta = metadata.get("runtime") if isinstance(metadata, dict) else {}
        requires = runtime_meta.get("requires") if isinstance(runtime_meta, dict) else {}
        bins = requires.get("bins") if isinstance(requires, dict) else []
        if isinstance(bins, list) and not use_skill_containers:
            missing = [b for b in bins if isinstance(b, str) and b.strip() and not _has_required_bin(b)]
            if missing:
                continue
        loaded[skill_id] = LoadedSkill(
            skill_id=skill_id,
            skill_dir=skill_dir,
            script_path=script_path,
            description=description,
            metadata=metadata,
        )
    return loaded
