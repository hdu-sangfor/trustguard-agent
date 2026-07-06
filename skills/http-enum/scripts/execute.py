from __future__ import annotations

import json
import re
import subprocess
import sys
import time


def _extract_title(body: str) -> str | None:
    m = re.search(r"<title>(.*?)</title>", body or "", flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()[:300]


def _split_http_sections(raw: str) -> tuple[str, str]:
    """
    兼容 CRLF/LF；若存在重定向链，取最后一段 HTTP 响应头+正文。
    """
    text = raw or ""
    # 支持 \r\n\r\n 与 \n\n 两种分隔
    sections = re.split(r"\r?\n\r?\n", text)
    if not sections:
        return "", ""
    # 选取最后一个包含 HTTP/ 起始行的段作为 header_block
    header_idx = -1
    for i, sec in enumerate(sections):
        if sec.strip().startswith("HTTP/"):
            header_idx = i
    if header_idx < 0:
        return "", text
    header_block = sections[header_idx]
    body = "\n\n".join(sections[header_idx + 1:]) if header_idx + 1 < len(sections) else ""
    return header_block, body


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    # 与 curl-raw / whatweb-fingerprint 一致：params.url 优先于 target
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

    timeout = str(int(params.get("timeout") or 20))
    cmd = ["curl", "-sS", "-i", "-L", "--max-time", timeout, url]
    run_timeout = int(timeout) + 5
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=run_timeout)
    except subprocess.TimeoutExpired:
        out = {
            "status": "TIMEOUT",
            "parsed_artifacts": {
                "url": url,
                "command": " ".join(cmd),
                "error": f"http-enum subprocess timeout after {run_timeout}s",
            },
            "raw_stdout": "",
            "raw_stderr": f"timeout after {run_timeout}s",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    raw = proc.stdout or ""
    header_block, body = _split_http_sections(raw)

    headers = {}
    status = None
    for i, line in enumerate(header_block.splitlines()):
        if i == 0 and line.startswith("HTTP/"):
            status = line.split(" ", 2)[1] if " " in line else None
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    parsed = {
        "url": url,
        "http_status": status,
        "headers": headers,
        "title": _extract_title(body),
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "raw_preview": raw[:2000],
    }
    out = {
        "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
        "parsed_artifacts": parsed,
        "raw_stdout": raw,
        "raw_stderr": proc.stderr or "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

