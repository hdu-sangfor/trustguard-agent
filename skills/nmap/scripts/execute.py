from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

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
    exe = str(info.get("executable") or "nmap").strip() or "nmap"
    if not rel_path:
        return exe, None
    tools_root = Path(os.getenv("TRUSTGUARD_TOOLS_ROOT") or (_project_root() / "TRUSTGUARD_TOOLS_ROOT"))
    skill_root = (tools_root / rel_path).resolve()
    return str(skill_root / exe), str(skill_root)


def _strip_url_to_host(target: str) -> str:
    """Strip http(s) scheme, port, and path from target so nmap receives host/IP only."""
    t = (target or "").strip()
    if "://" in t:
        try:
            host = urlparse(t).hostname or ""
            if host:
                return host
        except Exception:
            pass
    return t


def _build_cmd(target: str, params: dict, info: dict) -> list[str]:
    exe, _ = _resolve_executable(info)
    # nmap needs bare host/IP; strip any http(s) scheme, port, or path the LLM may include
    nmap_target = _strip_url_to_host(target)
    cmd = [exe, "-sV", "-sC", "-Pn"]

    ports = params.get("ports")
    if ports:
        cmd.extend(["-p", str(ports)])

    top_ports = params.get("top_ports")
    if top_ports:
        cmd.extend(["--top-ports", str(top_ports)])

    scripts = params.get("scripts")
    if isinstance(scripts, list) and scripts:
        cmd.extend(["--script", ",".join(str(s) for s in scripts if str(s).strip())])
    elif isinstance(scripts, str) and scripts.strip():
        cmd.extend(["--script", scripts.strip()])

    extra = params.get("extra")
    if isinstance(extra, list):
        cmd.extend([str(x) for x in extra if str(x).strip()])
    elif isinstance(extra, str) and extra.strip():
        cmd.extend(shlex.split(extra))

    # 兼容历史参数：Decision 层常传 params.args（原始 nmap 参数串）
    raw_args = params.get("args")
    if not raw_args:
        raw_args = params.get("arguments")
    if isinstance(raw_args, list):
        cmd.extend([str(x) for x in raw_args if str(x).strip()])
    elif isinstance(raw_args, str) and raw_args.strip():
        cmd.extend(shlex.split(raw_args))

    cmd.append(nmap_target)
    return cmd


def _parse_nmap(stdout: str) -> dict:
    open_ports: list[int] = []
    services: list[dict] = []
    states: dict[str, list[int]] = {}
    pat = re.compile(r"^(\d+)/(tcp|udp)\s+([a-zA-Z|]+)\s+([a-zA-Z0-9_.?-]+)")
    for line in (stdout or "").splitlines():
        m = pat.search(line.strip())
        if not m:
            continue
        port = int(m.group(1))
        state = m.group(3).lower()
        service = m.group(4)
        states.setdefault(state, []).append(port)
        if state == "open":
            open_ports.append(port)
            services.append({"port": port, "name": service})
    parsed = {"port_states": states} if states else {}
    if open_ports:
        parsed["open_ports"] = open_ports
        parsed["services"] = services
    return parsed


def main() -> int:
    start = time.perf_counter()
    payload = {}
    if len(sys.argv) > 1 and sys.argv[1].strip():
        payload = json.loads(sys.argv[1])

    target = str(payload.get("target") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    info = _load_tool_info("nmap")
    cmd = _build_cmd(target, params, info)
    _, cwd = _resolve_executable(info)
    timeout = int(params.get("timeout") or 120)
    hard_cap = int(os.getenv("NMAP_HARD_TIMEOUT_SECONDS", "120"))
    if timeout <= 0:
        timeout = 120
    timeout = min(timeout, hard_cap)

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parsed = _parse_nmap(proc.stdout or "")
        if not parsed:
            parsed = {"raw_preview": (proc.stdout or "")[:2000]}
        parsed["command"] = " ".join(shlex.quote(p) for p in cmd)
        parsed["returncode"] = proc.returncode
        out = {
            "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
            "parsed_artifacts": parsed,
            "raw_stdout": proc.stdout or "",
            "raw_stderr": proc.stderr or "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except FileNotFoundError:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "nmap executable not found", "command": " ".join(cmd)},
            "raw_stdout": "",
            "raw_stderr": "nmap executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    except subprocess.TimeoutExpired:
        out = {
            "status": "TIMEOUT",
            "parsed_artifacts": {"error": "nmap timeout", "command": " ".join(cmd)},
            "raw_stdout": "",
            "raw_stderr": f"timeout after {timeout}s",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
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

