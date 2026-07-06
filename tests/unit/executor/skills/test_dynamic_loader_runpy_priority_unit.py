from __future__ import annotations

import json
from pathlib import Path

from tests.executor_test_env import executor_sys_path_isolated


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_detect_script_path_prefers_run_py(tmp_path: Path) -> None:
    with executor_sys_path_isolated():
        from app.skills.dynamic_loader import _detect_script_path

        skill_dir = tmp_path / "skills" / "demo-skill"
        _write(skill_dir / "run.py", "print('run')\n")
        _write(skill_dir / "scripts" / "execute.py", "print('execute')\n")
        _write(
            skill_dir / "_meta.json",
            json.dumps({"entrypoint": "scripts/execute.py"}, ensure_ascii=False),
        )

        got = _detect_script_path(skill_dir)

        assert got == skill_dir / "run.py"


def test_detect_script_path_falls_back_to_meta_then_execute(tmp_path: Path) -> None:
    with executor_sys_path_isolated():
        from app.skills.dynamic_loader import _detect_script_path

        skill_dir = tmp_path / "skills" / "demo-skill"
        _write(
            skill_dir / "_meta.json",
            json.dumps({"entrypoint": "scripts/custom.py"}, ensure_ascii=False),
        )
        _write(skill_dir / "scripts" / "custom.py", "print('custom')\n")
        _write(skill_dir / "scripts" / "execute.py", "print('execute')\n")

        got = _detect_script_path(skill_dir)

        assert got == skill_dir / "scripts" / "custom.py"
