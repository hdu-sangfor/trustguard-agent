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
    url = str(params.get("url") or payload.get("target") or "").strip()
    if not url:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.url or target required"},
            "raw_stdout": "",
            "raw_stderr": "missing url",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"

    timeout = int(params.get("timeout") or 60)
    cmd = ["whatweb", "--log-brief=-", url]
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
            "parsed_artifacts": {"error": "whatweb not found"},
            "raw_stdout": "",
            "raw_stderr": "whatweb executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    raw = proc.stdout or ""
    parsed_lines = [line.strip() for line in raw.splitlines() if line.strip()]
    out = {
        "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
        "parsed_artifacts": {
            "url": url,
            "fingerprints": parsed_lines[:30],
            "raw_preview": raw[:2000],
        },
        "raw_stdout": raw,
        "raw_stderr": proc.stderr or "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
