from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import yaml


def _project_root() -> Path:
    p = Path(__file__).resolve()
    return p.parents[3] if len(p.parents) > 3 else Path("/skill")


def _registry_file() -> Path:
    env_path = os.getenv("TOOLS_REGISTRY_YAML", "").strip()
    if env_path:
        return Path(env_path)
    root = _project_root()
    for candidate in (root / "docker" / "tools_registry.yaml", root / "tools_registry.yaml"):
        if candidate.exists():
            return candidate
    return root / "docker" / "tools_registry.yaml"


def _load_tool_info(skill_id: str) -> dict:
    registry_file = _registry_file()
    if not registry_file.exists():
        return {}
    data = yaml.safe_load(registry_file.read_text(encoding="utf-8")) or {}
    tools = data.get("tools") or {}
    info = tools.get(skill_id) if isinstance(tools.get(skill_id), dict) else {}
    return info or {}


def _resolve_executable(info: dict) -> tuple[str, str | None]:
    rel_path = str(info.get("path") or "").strip()
    exe = str(info.get("executable") or "sqlmap.py").strip() or "sqlmap.py"
    if not rel_path:
        if Path("/opt/sqlmap/sqlmap.py").exists():
            return "/opt/sqlmap/sqlmap.py", "/opt/sqlmap"
        return exe, None
    tools_root = Path(os.getenv("TRUSTGUARD_TOOLS_ROOT") or (_project_root() / "TRUSTGUARD_TOOLS_ROOT"))
    skill_root = (tools_root / rel_path).resolve()
    return str(skill_root / exe), str(skill_root)


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    target = str(payload.get("target") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    info = _load_tool_info("sqlmap")
    exe, cwd = _resolve_executable(info)
    cmd = [sys.executable, exe, "-u", target, "--batch"]
    extra = params.get("extra")
    if isinstance(extra, list):
        cmd.extend([str(x) for x in extra if str(x).strip()])
    elif isinstance(extra, str) and extra.strip():
        cmd.extend(shlex.split(extra))

    timeout = int(params.get("timeout") or 600)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out = {
            "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
            "parsed_artifacts": {
                "raw_summary": (proc.stdout or "").strip()[:2000],
                "command": " ".join(shlex.quote(p) for p in cmd),
                "returncode": proc.returncode,
            },
            "raw_stdout": proc.stdout or "",
            "raw_stderr": proc.stderr or "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as exc:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": str(exc), "command": " ".join(cmd)},
            "raw_stdout": "",
            "raw_stderr": str(exc),
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

