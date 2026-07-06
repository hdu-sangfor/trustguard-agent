from __future__ import annotations

import json
import subprocess
import sys
import time
import tempfile
import os


def _build_target(target: str) -> str:
    if target.startswith(("http://", "https://")):
        return target
    return f"http://{target}"


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    raw_target = str(params.get("url") or payload.get("target") or "").strip()
    
    if not raw_target:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.url or target required"},
            "raw_stdout": "",
            "raw_stderr": "missing target",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    target = _build_target(raw_target)
    timeout = int(params.get("timeout") or 600)
    
    mode = str(params.get("mode") or "scan").strip().lower()
    
    output_file = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt")
    output_file_path = output_file.name
    output_file.close()

    cmd = ["python", "-m", "fenjing"]
    
    if mode == "scan":
        cmd.extend(["scan", "--url", target])
    elif mode == "crack":
        inputs = str(params.get("inputs") or "").strip()
        method = str(params.get("method") or "GET").strip().upper()
        
        cmd.extend(["crack", "--url", target, "--method", method])
        if inputs:
            cmd.extend(["--inputs", inputs])
    elif mode == "crack-path":
        inputs = str(params.get("inputs") or "").strip()
        cmd.extend(["crack-path", "--url", target])
        if inputs:
            cmd.extend(["--inputs", inputs])
    elif mode == "crack-json":
        json_data = str(params.get("json_data") or "").strip()
        key = str(params.get("key") or "").strip()
        
        if not json_data or not key:
            out = {
                "status": "FAILED",
                "parsed_artifacts": {"error": "params.json_data and params.key required for crack-json mode"},
                "raw_stdout": "",
                "raw_stderr": "missing json_data or key",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
            print(json.dumps(out, ensure_ascii=False))
            return 1
        
        cmd.extend(["crack-json", "--url", target, "--json-data", json_data, "--key", key])
    elif mode == "crack-request":
        request_file = str(params.get("request_file") or "").strip()
        host = str(params.get("host") or "").strip()
        port = str(params.get("port") or "80").strip()
        
        if not request_file:
            out = {
                "status": "FAILED",
                "parsed_artifacts": {"error": "params.request_file required for crack-request mode"},
                "raw_stdout": "",
                "raw_stderr": "missing request_file",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
            print(json.dumps(out, ensure_ascii=False))
            return 1
        
        cmd.extend(["crack-request", "-f", request_file, "--host", host, "--port", port])
    else:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": f"unknown mode: {mode}, supported: scan, crack, crack-path, crack-json, crack-request"},
            "raw_stdout": "",
            "raw_stderr": f"invalid mode: {mode}",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    extra = params.get("extra")
    if isinstance(extra, list):
        cmd.extend([str(x) for x in extra if str(x).strip()])
    elif isinstance(extra, str) and extra.strip():
        import shlex
        cmd.extend(shlex.split(extra))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "fenjing not found"},
            "raw_stdout": "",
            "raw_stderr": "fenjing executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    except subprocess.TimeoutExpired:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "fenjing scan timeout"},
            "raw_stdout": "",
            "raw_stderr": f"scan timed out after {timeout} seconds",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    parsed = {
        "target": target,
        "mode": mode,
        "vulnerability_found": False,
        "commands_executed": [],
        "output_lines": []
    }

    stdout_lines = (proc.stdout or "").splitlines()
    stderr_lines = (proc.stderr or "").splitlines()

    for line in stdout_lines:
        if line.strip():
            parsed["output_lines"].append(line.strip())

    for line in stdout_lines:
        if "success" in line.lower() or "vulnerability" in line.lower() or "found" in line.lower():
            parsed["vulnerability_found"] = True
        if "command:" in line.lower() or "executing" in line.lower():
            parsed["commands_executed"].append(line.strip())

    for line in stderr_lines:
        if line.strip() and line.strip() not in parsed["output_lines"]:
            parsed["output_lines"].append(f"[ERROR] {line.strip()}")

    out = {
        "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
        "parsed_artifacts": parsed,
        "raw_stdout": proc.stdout or "",
        "raw_stderr": proc.stderr or "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())