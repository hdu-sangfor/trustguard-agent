from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path


_WS_LINE = re.compile(r"^\s*https?://\S+")


def _safe_resolve_file(workspace_root: Path, rel: str) -> Path | None:
    raw = (rel or "").strip().replace("\\", "/")
    if not raw or ".." in Path(raw).parts:
        return None
    cand = (workspace_root / raw).resolve()
    try:
        cand.relative_to(workspace_root.resolve())
    except Exception:
        return None
    if not cand.is_file():
        return None
    return cand


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    # executor 启动 skill 容器时会注入 WORKSPACE_ROOT 环境变量；
    # params.workspace_dir 优先，否则读环境变量，最后回退 /data/workspace。
    workspace_root = Path(
        str(params.get("workspace_dir") or os.getenv("WORKSPACE_ROOT") or "/data/workspace")
    ).resolve()

    rel_path = str(params.get("rel_path") or params.get("path") or "").strip()
    max_urls = int(params.get("max_urls") or 800)
    if max_urls <= 0:
        max_urls = 800
    max_urls = min(max_urls, 5000)

    if not rel_path:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "params.rel_path required (relative to workspace)"},
            "raw_stdout": "",
            "raw_stderr": "missing rel_path",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    target = _safe_resolve_file(workspace_root, rel_path)
    if target is None:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {
                "error": "file not found or outside workspace",
                "rel_path": rel_path,
            },
            "raw_stdout": "",
            "raw_stderr": "invalid rel_path",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    try:
        raw_text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": str(e), "rel_path": rel_path},
            "raw_stdout": "",
            "raw_stderr": "read failed",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    lines = raw_text.splitlines()
    urls: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(("http://", "https://")):
            urls.append(s)
        else:
            m = _WS_LINE.match(ln)
            if m:
                urls.append(m.group(0).strip())
        if len(urls) >= max_urls:
            break

    truncated = len(urls) >= max_urls

    out = {
        "status": "SUCCESS",
        "parsed_artifacts": {
            "rel_path": rel_path,
            "resolved_path": str(target),
            "urls": urls,
            "url_count": len(urls),
            "total_lines": len(lines),
            "truncated": truncated,
        },
        "raw_stdout": "",
        "raw_stderr": "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
