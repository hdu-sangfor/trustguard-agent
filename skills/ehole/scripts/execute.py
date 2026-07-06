from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _normalize_url(raw: str) -> str:
    u = (raw or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"http://{u}"
    return u


def _extract_fingerprints(raw: str) -> list[str]:
    lines: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if any(k in low for k in ("finger", "cms", "framework", "server", "title", "status", "|")):
            lines.append(s[:300])
    if lines:
        return lines[:30]
    fallback = [ln.strip()[:300] for ln in (raw or "").splitlines() if ln.strip()]
    return fallback[:20]


def _parse_ehole_json(path: str) -> list[dict[str, object]]:
    try:
        if not os.path.isfile(path):
            return []
        raw = open(path, encoding="utf-8", errors="ignore").read().strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]
    except Exception:
        return []
    return []


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    target_url = _normalize_url(str(params.get("url") or payload.get("target") or ""))
    if not target_url:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.url or target required"},
            "raw_stdout": "",
            "raw_stderr": "missing target url",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    timeout_seconds = int(params.get("timeout") or 30)
    threads = int(params.get("threads") or params.get("thread") or 20)
    threads = max(5, min(threads, 100))

    json_path = ""
    try:
        import tempfile

        jf = tempfile.NamedTemporaryFile(prefix="ehole-out-", suffix=".json", delete=False)
        json_path = jf.name
        jf.close()
    except Exception:
        json_path = ""

    cmd = ["ehole", "finger", "-u", target_url, "-t", str(threads)]
    if json_path:
        cmd.extend(["-o", json_path])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        out = {
            "status": "TIMEOUT",
            "parsed_artifacts": {
                "input_url": target_url,
                "error": f"ehole timeout after {timeout_seconds}s",
                "command": " ".join(cmd),
            },
            "raw_stdout": "",
            "raw_stderr": f"timeout after {timeout_seconds}s",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        if json_path:
            try:
                os.unlink(json_path)
            except OSError:
                pass
        return 1
    except FileNotFoundError:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "ehole executable not found"},
            "raw_stdout": "",
            "raw_stderr": "ehole not found in PATH",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        if json_path:
            try:
                os.unlink(json_path)
            except OSError:
                pass
        return 1

    raw_out = proc.stdout or ""
    raw_err = proc.stderr or ""
    combined_err = f"{raw_err}\n{raw_out}".lower()
    fingerprint_load_err = "fingerprint file error" in combined_err

    rows = _parse_ehole_json(json_path) if json_path else []
    json_fps: list[str] = []
    for row in rows:
        cms = str(row.get("cms") or "").strip()
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        bits = [x for x in (cms, title, url) if x]
        if bits:
            json_fps.append(" | ".join(bits)[:400])

    fps = json_fps if json_fps else _extract_fingerprints(raw_out)
    parsed_artifacts: dict[str, object] = {
        "input_url": target_url,
        "fingerprints": fps,
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "raw_preview": raw_out[:2000],
        "ehole_exit_quirk_note": (
            "EHole finger subcommand calls os.Exit(1) after scan even on success; "
            "executor treats rc=1 as success when output/json present and fingerprint DB loaded."
        ),
    }
    if raw_err:
        parsed_artifacts["stderr_preview"] = raw_err[:500]
    if rows:
        parsed_artifacts["structured_rows"] = rows[:20]

    # 上游 cmd/finger.go 在 Run 末尾固定 os.Exit(1)，不能仅用 returncode 判断成败。
    has_signal = bool(fps) or bool(raw_out.strip()) or bool(rows)
    scan_ok = not fingerprint_load_err and has_signal and proc.returncode in (0, 1)
    strict_ok = proc.returncode == 0 and not fingerprint_load_err

    status = "SUCCESS" if (strict_ok or scan_ok) else "FAILED"
    ok = status == "SUCCESS"

    if json_path:
        try:
            os.unlink(json_path)
        except OSError:
            pass

    out = {
        "status": status,
        "parsed_artifacts": parsed_artifacts,
        "raw_stdout": raw_out,
        "raw_stderr": raw_err,
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
