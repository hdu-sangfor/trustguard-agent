from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def _auto_quote_url_path(url: str, enabled: bool) -> tuple[str, bool]:
    if not enabled:
        return url, False
    try:
        parts = urlsplit(url)
        path = parts.path or ""
        if not path:
            return url, False
        # 先解码再编码，避免已编码 payload 被二次编码（%24 -> %2524）。
        decoded_path = unquote(path)
        # 仅重编码路径中的高风险字符，保留 URI 常见保留字符。
        quoted_path = quote(decoded_path, safe="/:@-._~!&'()*+,;=")
        if quoted_path == path:
            return url, False
        return urlunsplit((parts.scheme, parts.netloc, quoted_path, parts.query, parts.fragment)), True
    except Exception:
        return url, False


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
    auto_urlencode_path = bool(params.get("auto_urlencode_path", True))
    url, path_quoted = _auto_quote_url_path(url, enabled=auto_urlencode_path)

    method = str(params.get("method") or "GET").upper()
    timeout = str(int(params.get("timeout") or 20))
    cmd = ["curl", "-sS", "-i", "--max-time", timeout, "-X", method]
    if params.get("follow_redirects", True):
        cmd.append("-L")

    headers = params.get("headers")
    headers_map = headers if isinstance(headers, dict) else {}
    multipart = params.get("multipart")
    if multipart is None:
        # 明确支持 multipart_fields 字典参数，避免 LLM 手搓 body。
        multipart = params.get("multipart_fields")
    used_multipart_builder = False
    if isinstance(headers, dict):
        for key, value in headers.items():
            if isinstance(multipart, (list, dict)) and str(key).strip().lower() == "content-type":
                # multipart 由 curl -F 自动注入 boundary，手工覆盖会导致 Header/Body 边界失配。
                continue
            cmd.extend(["-H", f"{key}: {value}"])

    if isinstance(multipart, list) and multipart:
        for part in multipart:
            if not isinstance(part, dict):
                continue
            name = str(part.get("name") or "").strip()
            if not name:
                continue
            if "file" in part:
                file_path = Path(str(part.get("file") or "")).expanduser()
                if file_path.exists():
                    cmd.extend(["-F", f"{name}=@{str(file_path)}"])
                continue
            value = str(part.get("value") or "")
            cmd.extend(["-F", f"{name}={value}"])
        used_multipart_builder = True
    elif isinstance(multipart, dict):
        for name, value in multipart.items():
            n = str(name or "").strip()
            if not n:
                continue
            cmd.extend(["-F", f"{n}={value}"])
        used_multipart_builder = True

    if not used_multipart_builder and params.get("data") is not None:
        cmd.extend(["--data", str(params.get("data"))])
    if not used_multipart_builder and params.get("data_binary") is not None:
        cmd.extend(["--data-binary", str(params.get("data_binary"))])

    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    raw = proc.stdout or ""
    status_line = raw.splitlines()[0] if raw.splitlines() else ""

    out = {
        "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
        "parsed_artifacts": {
            "url": url,
            "method": method,
            "status_line": status_line,
            "path_auto_urlencoded": path_quoted,
            "multipart_builder_used": used_multipart_builder,
            "content_type_header_removed": bool(
                used_multipart_builder and isinstance(headers_map, dict) and any(str(k).strip().lower() == "content-type" for k in headers_map.keys())
            ),
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
