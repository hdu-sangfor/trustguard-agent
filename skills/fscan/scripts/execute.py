from __future__ import annotations

import json
import os
import re
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
    exe = str(info.get("executable") or "fscan").strip() or "fscan"
    if not rel_path:
        return exe, None
    tools_root = Path(os.getenv("TRUSTGUARD_TOOLS_ROOT") or (_project_root() / "TRUSTGUARD_TOOLS_ROOT"))
    skill_root = (tools_root / rel_path).resolve()
    return str(skill_root / exe), str(skill_root)


def _build_cmd(target: str, params: dict, info: dict) -> list[str]:
    exe, _ = _resolve_executable(info)
    
    if target.startswith(("http://", "https://")):
        cmd = [exe, "-u", target]
    else:
        cmd = [exe, "-h", target]

    ports = params.get("ports")
    if ports:
        cmd.extend(["-p", str(ports)])

    top_ports = params.get("top_ports")
    if top_ports:
        cmd.extend(["-top", str(top_ports)])

    modules = params.get("modules")
    if isinstance(modules, list) and modules:
        cmd.extend(["-m", ",".join(str(s) for s in modules if str(s).strip())])
    elif isinstance(modules, str) and modules.strip():
        cmd.extend(["-m", modules.strip()])

    extra = params.get("extra")
    if isinstance(extra, list):
        cmd.extend([str(x) for x in extra if str(x).strip()])
    elif isinstance(extra, str) and extra.strip():
        cmd.extend(shlex.split(extra))

    return cmd


def _parse_fscan(stdout: str) -> dict:
    open_ports: list[int] = []
    services: list[dict] = []
    vulnerabilities: list[dict] = []
    
    port_pat = re.compile(r"(\d+)\s+(tcp|udp)\s+open\s+(\S+)")
    vuln_pat = re.compile(r"\[(.*?)\].*?(?:vulnerability|CVE|weakness|vuln)", re.IGNORECASE)
    url_vuln_pat = re.compile(r"\[(.*?)\].*?\[P?O?C?\]", re.IGNORECASE)
    pocscan_pat = re.compile(r"\[\+\]\s+PocScan\s+(\S+)\s+(.+)", re.IGNORECASE)
    
    for line in (stdout or "").splitlines():
        line = line.strip()
        m = port_pat.search(line)
        if m:
            port = int(m.group(1))
            proto = m.group(2)
            service = m.group(3)
            open_ports.append(port)
            services.append({"port": port, "proto": proto, "name": service})
        
        m = vuln_pat.search(line)
        if m:
            vulnerabilities.append({"type": m.group(1), "line": line})
        
        m = url_vuln_pat.search(line)
        if m:
            vulnerabilities.append({"type": m.group(1), "line": line})
        
        m = pocscan_pat.search(line)
        if m:
            vulnerabilities.append({"type": m.group(2), "url": m.group(1), "line": line})
    
    parsed = {}
    if open_ports:
        parsed["open_ports"] = open_ports
        parsed["services"] = services
    if vulnerabilities:
        parsed["vulnerabilities"] = vulnerabilities
    if not parsed:
        parsed = {"raw_preview": (stdout or "")[:2000]}
    
    return parsed


def main() -> int:
    start = time.perf_counter()
    payload = {}
    if len(sys.argv) > 1 and sys.argv[1].strip():
        payload = json.loads(sys.argv[1])

    target = str(payload.get("target") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    info = _load_tool_info("fscan")
    cmd = _build_cmd(target, params, info)
    _, cwd = _resolve_executable(info)
    timeout = int(params.get("timeout") or 300)
    hard_cap = int(os.getenv("FSCAN_HARD_TIMEOUT_SECONDS", "300"))
    if timeout <= 0:
        timeout = 300
    timeout = min(timeout, hard_cap)

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parsed = _parse_fscan(proc.stdout or "")
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
            "parsed_artifacts": {"error": "fscan executable not found", "command": " ".join(cmd)},
            "raw_stdout": "",
            "raw_stderr": "fscan executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    except subprocess.TimeoutExpired:
        out = {
            "status": "TIMEOUT",
            "parsed_artifacts": {"error": "fscan timeout", "command": " ".join(cmd)},
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
