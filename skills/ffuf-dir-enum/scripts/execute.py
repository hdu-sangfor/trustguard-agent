from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time


_DEFAULT_WORDS = "\n".join(
    [
        "admin",
        "login",
        "api",
        "backup",
        "test",
        "uploads",
        "robots.txt",
        ".git",
    ]
)


def _to_int(v) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _dedupe_by_size_words(items: list[dict], enable: bool) -> tuple[list[dict], int]:
    if not enable:
        return items, 0
    kept: list[dict] = []
    seen_signatures: set[tuple[int | None, int | None]] = set()
    dropped = 0
    for item in items:
        size = _to_int(item.get("length"))
        words = _to_int(item.get("words"))
        signature = (size, words)
        if signature in seen_signatures:
            dropped += 1
            continue
        seen_signatures.add(signature)
        kept.append(item)
    return kept, dropped


def _build_target(target: str) -> str:
    if "FUZZ" in target:
        return target
    if not target.startswith(("http://", "https://")):
        target = f"http://{target}"
    if target.endswith("/"):
        return f"{target}FUZZ"
    return f"{target}/FUZZ"


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
    timeout = int(params.get("timeout") or 120)
    threads = str(int(params.get("threads") or 20))
    mc = str(params.get("mc") or "200,204,301,302,307,401,403")
    dedupe = str(params.get("dedupe_size_words") or "true").lower() not in ("0", "false", "no")

    wl = str(params.get("wordlist") or "").strip()
    tmp_file = None
    if not wl:
        tmp_file = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        tmp_file.write(_DEFAULT_WORDS)
        tmp_file.flush()
        wl = tmp_file.name

    cmd = ["ffuf", "-u", target, "-w", wl, "-t", threads, "-mc", mc, "-of", "json", "-o", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "ffuf not found"},
            "raw_stdout": "",
            "raw_stderr": "ffuf executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    finally:
        if tmp_file is not None:
            try:
                os.unlink(tmp_file.name)
            except Exception:
                pass

    parsed = {}
    try:
        data = json.loads(proc.stdout or "{}")
        results = data.get("results") if isinstance(data, dict) else []
        if isinstance(results, list):
            rows = [item for item in results if isinstance(item, dict)]
            filtered_rows, dropped = _dedupe_by_size_words(rows, dedupe)
            parsed = {
                "target": target,
                "dedupe_size_words": dedupe,
                "dropped_duplicates": dropped,
                "matches": [
                    {
                        "url": item.get("url"),
                        "status": item.get("status"),
                        "length": item.get("length"),
                        "words": item.get("words"),
                    }
                    for item in filtered_rows[:100]
                ],
            }
    except Exception:
        parsed = {"target": target, "raw_preview": (proc.stdout or "")[:2000]}

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
