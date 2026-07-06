from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    query = params.get("query") or params.get("q") or payload.get("target") or ""
    if not str(query).strip():
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "query required"},
            "raw_stdout": "",
            "raw_stderr": "params.query or target required",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    if not os.getenv("BAIDU_API_KEY"):
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "BAIDU_API_KEY not set"},
            "raw_stdout": "",
            "raw_stderr": "BAIDU_API_KEY not set",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    body = {"query": query}
    for key in (
        "edition",
        "resource_type_filter",
        "search_filter",
        "block_websites",
        "search_recency_filter",
        "safe_search",
    ):
        if key in params:
            body[key] = params[key]

    search_script = Path(__file__).resolve().parent / "search.py"
    timeout = int(params.get("timeout") or 30)
    proc = subprocess.run(
        [sys.executable, str(search_script), json.dumps(body, ensure_ascii=False)],
        cwd=str(search_script.parent),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    parsed = None
    output = proc.stdout or ""
    for line in reversed(output.strip().splitlines() if output.strip() else []):
        line = line.strip()
        if not (line.startswith("[") or line.startswith("{")):
            continue
        try:
            parsed = json.loads(line)
            break
        except Exception:
            continue

    if parsed is None:
        parsed = {"raw_preview": output[:2000]}
    elif isinstance(parsed, list):
        parsed = {"references": parsed}

    out = {
        "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
        "parsed_artifacts": parsed,
        "raw_stdout": output,
        "raw_stderr": proc.stderr or "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

