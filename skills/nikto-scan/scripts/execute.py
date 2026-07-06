from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    target = str(params.get("url") or payload.get("target") or "").strip()
    if not target:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.url or target required"},
            "raw_stdout": "",
            "raw_stderr": "missing target",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    if not target.startswith(("http://", "https://")):
        target = f"http://{target}"

    timeout = int(params.get("timeout") or 300)
    cmd = ["nikto", "-h", target, "-Format", "txt", "-Display", "V"]
    extra = params.get("extra")
    if isinstance(extra, list):
        cmd.extend([str(x) for x in extra if str(x).strip()])
    elif isinstance(extra, str) and extra.strip():
        cmd.extend(shlex.split(extra))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "nikto not found"},
            "raw_stdout": "",
            "raw_stderr": "nikto executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    findings = [line for line in lines if line.startswith("+")]
    out = {
        "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
        "parsed_artifacts": {
            "target": target,
            "findings": findings[:200],
            "raw_preview": (proc.stdout or "")[:3000],
        },
        "raw_stdout": proc.stdout or "",
        "raw_stderr": proc.stderr or "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
