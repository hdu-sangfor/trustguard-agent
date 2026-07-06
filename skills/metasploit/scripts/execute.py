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
    if not info:
        return {}
    return info


def _build_cmd(target: str, params: dict, info: dict) -> list[str]:
    exe = str(info.get("executable") or "/usr/src/metasploit-framework/msfconsole").strip() or "/usr/src/metasploit-framework/msfconsole"
    
    modules = params.get("modules")
    options = params.get("options") if isinstance(params.get("options"), dict) else {}
    mode = str(params.get("mode") or "shell").strip().lower()
    
    # Handle different module parameter formats
    if not modules:
        modules = params.get("exploit")
    if not modules:
        modules = params.get("module")
    
    if isinstance(modules, list) and modules:
        module = str(modules[0]).strip()
    elif isinstance(modules, str) and modules.strip():
        module = modules.strip()
    else:
        module = ""
    
    commands = []
    if module:
        if module.startswith("search"):
            commands.append(module)
        else:
            commands.append(f"use {module}")
    
    if target and not module.startswith("search"):
        if target.startswith(("http://", "https://")):
            match = re.match(r"https?://([^:/]+)(?::(\d+))?", target)
            if match:
                rhosts = match.group(1)
                rport = match.group(2) or "80"
            else:
                rhosts = target
                rport = "80"
            
            # Extract path for TARGETURI
            from urllib.parse import urlparse
            parsed_url = urlparse(target)
            if parsed_url.path and parsed_url.path != "/":
                commands.append(f"set TARGETURI {parsed_url.path}")
        else:
            rhosts = target
            rport = None
        
        commands.append(f"set RHOSTS {rhosts}")
        if rport:
            commands.append(f"set RPORT {rport}")
    
    use_check = False
    is_command_mode = mode == "command"
    
    # Handle LHOST and LPORT for reverse shells
    lhost = None
    lport = None
    
    # Merge options from params.options and direct params (for compatibility)
    merged_options = {}
    if isinstance(options, dict):
        merged_options.update(options)
    
    # Add common Metasploit options directly from params if not in options (case-insensitive)
    option_mappings = {
        "lhost": "LHOST",
        "lport": "LPORT", 
        "payload": "PAYLOAD",
        "cmd": "CMD",
        "targeturi": "TARGETURI",
        "command": "command",
        "fetchhandlerdisable": "FetchHandlerDisable",
        "fetchlistenerbindport": "FetchListenerBindPort"
    }
    
    for lower_key, upper_key in option_mappings.items():
        if lower_key in params and upper_key not in merged_options:
            merged_options[upper_key] = params[lower_key]
        elif upper_key in params and upper_key not in merged_options:
            merged_options[upper_key] = params[upper_key]
    
    if merged_options and not module.startswith("search"):
        for key, value in merged_options.items():
            if value is not None and str(value).strip():
                if key.lower() == "command" and value.lower() == "check":
                    use_check = True
                elif key.lower() == "lhost":
                    lhost = value
                    commands.append(f"set LHOST {value}")
                elif key.lower() == "lport":
                    lport = value
                    commands.append(f"set LPORT {value}")
                elif key.lower() == "payload":
                    commands.append(f"set PAYLOAD {value}")
                elif key.lower() == "fetchhandlerdisable":
                    commands.append(f"set FetchHandlerDisable {value}")
                elif key.lower() == "fetchlistenerbindport":
                    commands.append(f"set FetchListenerBindPort {value}")
                elif key.lower() != "command":
                    if is_command_mode and key.lower() in ["lhost", "lport", "payload"]:
                        continue
                    commands.append(f"set {key} {value}")
    
    # Let Metasploit use its own default payload instead of forcing Windows payload
    # The exploit will use its default payload which is more appropriate for the target
    
    if commands and not module.startswith("search"):
        if use_check:
            commands.append("check")
        else:
            commands.append("run")
        commands.append("exit")
    elif commands:
        commands.append("exit")
    
    script_content = ";".join(commands)
    
    return [exe, "-q", "-d", "-x", script_content]


def _parse_msfconsole(stdout: str, stderr: str) -> dict:
    results = []
    vulnerabilities = []
    sessions = []
    
    patterns = [
        r"Exploit completed, but no session was created",
        r"Exploit succeeded",
        r"Session \d+ opened",
        r"vulnerability detected",
        r"Vulnerable",
        r"CVE-\d{4}-\d{4,}",
        r"exploit/\S+",
        r"auxiliary/\S+",
    ]
    
    combined = (stdout or "") + (stderr or "")
    
    for line in combined.splitlines():
        line = line.strip()
        if not line:
            continue
        
        if re.search(r"Session \d+ opened", line, re.IGNORECASE):
            m = re.search(r"Session (\d+) opened", line, re.IGNORECASE)
            if m:
                sessions.append({"session_id": m.group(1), "line": line})
        
        if re.search(r"Exploit succeeded", line, re.IGNORECASE) or re.search(r"vulnerability detected", line, re.IGNORECASE):
            vulnerabilities.append({"line": line})
        
        for pattern in patterns:
            if re.search(pattern, line, re.IGNORECASE) and "msfconsole" not in line.lower():
                results.append({"line": line})
                break
    
    parsed = {}
    if sessions:
        parsed["sessions"] = sessions
    if vulnerabilities:
        parsed["vulnerabilities"] = vulnerabilities
    if results:
        parsed["results"] = results
    if not parsed:
        parsed = {"raw_preview": (combined or "")[:2000]}
    
    return parsed


def _read_input() -> dict:
    payload = {}
    
    if len(sys.argv) > 1 and sys.argv[1].strip():
        try:
            payload = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            input_file = sys.argv[1].strip()
            if os.path.exists(input_file):
                payload = json.loads(Path(input_file).read_text(encoding="utf-8"))
    elif not sys.stdin.isatty():
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError:
            pass
    
    return payload


def main() -> int:
    start = time.perf_counter()
    
    payload = _read_input()
    
    target = str(payload.get("target") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    info = _load_tool_info("metasploit")
    cmd = _build_cmd(target, params, info)
    
    timeout = int(params.get("timeout") or 300)
    hard_cap = int(os.getenv("METASPLOIT_HARD_TIMEOUT_SECONDS", "300"))
    if timeout <= 0:
        timeout = 300
    timeout = min(timeout, hard_cap)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parsed = _parse_msfconsole(proc.stdout or "", proc.stderr or "")
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
            "parsed_artifacts": {"error": "msfconsole executable not found", "command": " ".join(cmd)},
            "raw_stdout": "",
            "raw_stderr": "msfconsole executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    except subprocess.TimeoutExpired:
        out = {
            "status": "TIMEOUT",
            "parsed_artifacts": {"error": "msfconsole timeout", "command": " ".join(cmd)},
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
