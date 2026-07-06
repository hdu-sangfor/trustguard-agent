from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


_URL_LIST_BASENAMES = frozenset({"katana_urls.txt", "clustered_targets.txt"})


def _safe_resolve_under_workspace(workspace_root: Path, ref: str) -> Path | None:
    raw = (ref or "").strip()
    if not raw:
        return None

    if raw.startswith("wsref:"):
        rel = raw[len("wsref:") :].strip().replace("\\", "/")
        parts = [x for x in rel.split("/") if x and x not in (".", "..")]
        if len(parts) == 3:
            cand = workspace_root / parts[0] / "artifacts" / parts[1] / parts[2]
        else:
            cand = workspace_root / Path("/".join(parts))
    else:
        p = Path(raw)
        cand = p if p.is_absolute() else (workspace_root / p)

    try:
        resolved = cand.resolve()
        resolved.relative_to(workspace_root.resolve())
        return resolved
    except Exception:
        return None


def _collect_candidate_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    priority: list[Path] = []
    for name in sorted(_URL_LIST_BASENAMES):
        for p in sorted(path.rglob(name)):
            if p.is_file():
                priority.append(p)
    for rel in ("parsed.json",):
        fp = path / rel
        if fp.is_file():
            priority.append(fp)
    tail = [
        path / "results" / "chunk_0001.jsonl",
        path / "raw.out",
        path / "raw.err",
        path / "meta.json",
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for p in priority + [x for x in tail if x.is_file()]:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    if out:
        return out
    all_files = [p for p in path.glob("**/*") if p.is_file()]
    all_files.sort(key=lambda x: (x.name not in _URL_LIST_BASENAMES, str(x)))
    return all_files[:12]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _http_lines_from_text(text: str, limit: int) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(("http://", "https://")):
            out.append(s)
            if len(out) >= limit:
                break
    return out


def main() -> int:
    start = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    # executor 启动 skill 容器时会注入 WORKSPACE_ROOT 环境变量；
    # params.workspace_dir 优先，否则读环境变量，最后回退 /data/workspace。
    workspace_root = Path(
        str(params.get("workspace_dir") or os.getenv("WORKSPACE_ROOT") or "/data/workspace")
    ).resolve()

    artifact_ref = str(params.get("artifact_ref") or "").strip()
    grep_keyword = str(params.get("grep_keyword") or "").strip()
    max_chars = int(params.get("max_chars") or 4000)
    if max_chars <= 0:
        max_chars = 4000
    max_chars = min(max_chars, 20000)
    list_file_max_chars = int(params.get("list_file_max_chars") or 14000)
    if list_file_max_chars <= 0:
        list_file_max_chars = 14000
    list_file_max_chars = min(list_file_max_chars, 18000)
    url_preview_max = min(int(params.get("url_preview_max") or 120), 500)

    target = _safe_resolve_under_workspace(workspace_root, artifact_ref)
    if target is None:
        # Empty or invalid ref: list available artifacts under the task workspace so LLM can pick a valid one
        task_id = str(payload.get("task_id") or params.get("task_id") or "").strip()
        available_dirs: list[str] = []
        if task_id:
            task_ws = workspace_root / task_id
            if task_ws.is_dir():
                for p in sorted(task_ws.iterdir()):
                    if p.is_dir():
                        available_dirs.append(str(p).replace("\\", "/"))
        if not available_dirs:
            # list root level if no task_id match
            for p in sorted(workspace_root.iterdir())[:20]:
                available_dirs.append(str(p).replace("\\", "/"))
        out = {
            "status": "SUCCESS",
            "parsed_artifacts": {
                "artifact_ref": artifact_ref,
                "note": "artifact_ref was empty; listing available workspace dirs for reference",
                "available_dirs": available_dirs[:20],
                "content": "No artifact_ref provided. Available workspace dirs:\n" + "\n".join(available_dirs[:20]),
            },
            "raw_stdout": "",
            "raw_stderr": "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0

    files = _collect_candidate_files(target)
    if not files:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {
                "error": "artifact not found",
                "artifact_ref": artifact_ref,
                "resolved_path": str(target),
            },
            "raw_stdout": "",
            "raw_stderr": "artifact not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1

    priority_files = [f for f in files if f.name in _URL_LIST_BASENAMES]
    rest_files = [f for f in files if f.name not in _URL_LIST_BASENAMES]

    chunks: list[str] = []
    matched_lines = 0
    used = 0
    urls_preview: list[str] = []

    def rel_str(f: Path) -> str:
        return str(f).replace("\\", "/")

    for f in priority_files:
        text = _read_text(f)
        if grep_keyword:
            lines = text.splitlines()
            keep = [ln for ln in lines if grep_keyword.lower() in ln.lower()]
            matched_lines += len(keep)
            text = "\n".join(keep)
        if not text:
            continue
        for u in _http_lines_from_text(text, url_preview_max - len(urls_preview)):
            urls_preview.append(u)
            if len(urls_preview) >= url_preview_max:
                break
        room = min(list_file_max_chars - used, max_chars - used)
        if room <= 0:
            break
        body = text if len(text) <= room else text[:room] + "\n...[TRUNCATED]"
        block = f"[FILE] {rel_str(f)}\n{body}"
        chunks.append(block)
        used += len(block) + 2
        if used >= max_chars:
            break

    for f in rest_files:
        if used >= max_chars:
            break
        text = _read_text(f)
        if grep_keyword:
            lines = text.splitlines()
            keep = [ln for ln in lines if grep_keyword.lower() in ln.lower()]
            matched_lines += len(keep)
            text = "\n".join(keep)
        if not text:
            continue
        room = max_chars - used
        if room <= 400:
            break
        body = text if len(text) <= room else text[:room] + "\n...[TRUNCATED]"
        block = f"[FILE] {rel_str(f)}\n{body}"
        chunks.append(block)
        used += len(block) + 2

    content = "\n\n".join(chunks)
    if len(content) > max_chars:
        content = content[:max_chars] + "\n...[TRUNCATED]"

    out = {
        "status": "SUCCESS",
        "parsed_artifacts": {
            "artifact_ref": artifact_ref,
            "resolved_path": str(target),
            "inspected_files": [str(p) for p in files],
            "priority_url_files": [rel_str(p) for p in priority_files],
            "grep_keyword": grep_keyword,
            "matched_lines": matched_lines if grep_keyword else None,
            "urls_preview": urls_preview,
            "urls_preview_max": url_preview_max,
            "content": content,
        },
        "raw_stdout": "",
        "raw_stderr": "",
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
