from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from workspace_resolve import resolve_under_workspace

TECH_EXCLUSION_MAP = {
    "php": [".php", ".php3", ".phtml", ".php5"],
    "asp": [".asp", ".aspx", ".ashx", ".asmx", ".asax"],
    "jsp": [".jsp", ".action", ".do", ".jspx"],
    "static": [".html", ".htm", ".css", ".js", ".png", ".jpg"],
}

DIRSEARCH_THREADS = 100
DIRSEARCH_MAX_RATE = 500
DIRSEARCH_REQUEST_TIMEOUT = 3
DIRSEARCH_PROCESS_TIMEOUT = 300
DIRSEARCH_RETRIES = 1
SAFE_MAX_RECURSION_DEPTH = "2"
SAFE_RECURSION_STATUSES = "200,301,302,403"
STATIC_EXCLUDED_SUBDIRS = [
    "image",
    "images",
    "css",
    "js",
    "style",
    "static",
    "font",
    "fonts",
]


def _to_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _build_target(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw
    return f"http://{raw}"


def _wordlist_base_path() -> Path:
    p = Path("/skill/wordlists/v1")
    if p.exists():
        return p
    return Path(__file__).resolve().parent.parent / "wordlists" / "v1"


def _registry_path(base: Path) -> Path:
    return base / "registry.json"


def _load_registry(base: Path) -> dict[str, Any]:
    p = _registry_path(base)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _legacy_tags_to_keywords(tags: list[str]) -> list[str]:
    mapping = {
        "java_full": "java",
        "java_core": "java",
        "struts2": "java",
        "spring": "java",
        "api_fuzz": "api",
        "api": "api",
        "generic": "admin",
        "generic_full": "sensitive",
    }
    out: list[str] = []
    for t in tags:
        k = mapping.get(t.lower())
        if k and k not in out:
            out.append(k)
    return out


def _safe_resolve(base: Path, rel: str) -> Path | None:
    p = (base / rel).resolve()
    try:
        p.relative_to(base.resolve())
    except Exception:
        return None
    return p


def _collect_ordered_files(base: Path, registry: dict[str, Any], keywords: list[str]) -> tuple[list[tuple[str, Path]], list[str]]:
    cfg = registry.get("config") if isinstance(registry.get("config"), dict) else {}
    enforce_private = bool(cfg.get("enforce_private", True))
    private_fail_policy = str(cfg.get("private_fail_policy", "hard_fail")).strip().lower()

    # private 优先于 seeds/base：避免 max_total_lines 在读完大词表后 outer-break 跳过 struct2/struts2 等短私有表
    groups: list[tuple[str, list[str]]] = [
        ("private", _to_list(registry.get("private"))),
        ("seeds", _to_list(registry.get("seeds"))),
        ("base", _to_list(registry.get("base"))),
    ]
    mappings = registry.get("mappings") if isinstance(registry.get("mappings"), dict) else {}
    open_files: list[str] = []
    for kw in keywords:
        open_files.extend(_to_list(mappings.get(kw)))
    groups.append(("open", open_files))

    missing: list[str] = []
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for group, rels in groups:
        for rel in rels:
            p = _safe_resolve(base, rel)
            if p is None or (not p.exists()) or (not p.is_file()):
                missing.append(f"{group}:{rel}")
                if group == "private" and enforce_private and private_fail_policy == "hard_fail":
                    raise RuntimeError(f"missing private wordlist: {rel}")
                continue
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            out.append((group, p))
    return out, missing


def _is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if not s.startswith("/"):
        return True
    body = s[1:]
    if body.isdigit():
        return True
    # 过滤明显随机短串路径
    if re.fullmatch(r"[A-Za-z0-9]{1,6}", body or ""):
        return True
    return False


def _apply_exclusion(line: str, exclude_patterns: list[str]) -> bool:
    low = line.lower()
    return any(p in low for p in exclude_patterns)


def _build_optimized_wordlist(
    base: Path,
    registry: dict[str, Any],
    keywords: list[str],
    exclude_techs: list[str],
) -> tuple[str, dict[str, Any]]:
    cfg = registry.get("config") if isinstance(registry.get("config"), dict) else {}
    max_total_lines = int(cfg.get("max_total_lines", 120000) or 120000)
    files, missing = _collect_ordered_files(base, registry, keywords)

    exclude_patterns: list[str] = []
    for tech in exclude_techs:
        exclude_patterns.extend(TECH_EXCLUSION_MAP.get(tech.lower(), []))

    dedup = OrderedDict()
    filtered_noise_count = 0
    filtered_exclusion_count = 0
    loaded_files: list[str] = []
    for group, fp in files:
        loaded_files.append(f"{group}:{str(fp.relative_to(base)).replace(chr(92), '/')}")
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if _is_noise_line(line):
                        filtered_noise_count += 1
                        continue
                    if _apply_exclusion(line, exclude_patterns):
                        filtered_exclusion_count += 1
                        continue
                    if len(dedup) >= max_total_lines:
                        break
                    if line not in dedup:
                        dedup[line] = 1
            # 不在此处 break：继续扫描后续词表文件，避免 private 行从未被读入
        except (PermissionError, OSError):
            missing.append(f"read_error:{str(fp.relative_to(base)).replace(chr(92), '/')}")
            continue

    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt", dir="/tmp", encoding="utf-8")
    tmp.write("\n".join(dedup.keys()))
    tmp.close()
    return tmp.name, {
        "keywords": keywords,
        "exclude_techs": exclude_techs,
        "loaded_files": loaded_files,
        "missing_files": missing,
        "total_lines": len(dedup),
        "max_total_lines": max_total_lines,
        "filtered_noise_count": filtered_noise_count,
        "filtered_exclusion_count": filtered_exclusion_count,
    }


def _norm_extensions(value: Any) -> str:
    exts = _to_list(value)
    if not exts:
        return "php,html,js,txt,jsp,do,action"
    return ",".join([e.lstrip(".").strip() for e in exts if e.strip()])


def _build_cmd(target: str, wl_path: str, output_path: str, extensions: str, enable_recursion: bool) -> list[str]:
    cmd = [
        "dirsearch",
        "-u",
        target,
        "-w",
        wl_path,
        "-t",
        str(DIRSEARCH_THREADS),
        "--max-rate",
        str(DIRSEARCH_MAX_RATE),
        "--timeout",
        str(DIRSEARCH_REQUEST_TIMEOUT),
        "--retries",
        str(DIRSEARCH_RETRIES),
        "-e",
        extensions,
        "-O",
        "json",
        "-o",
        output_path,
        "--exclude-subdirs",
        ",".join(STATIC_EXCLUDED_SUBDIRS),
        "--random-agent",
        "-q",
    ]
    if enable_recursion:
        cmd.extend(["-r", "-R", SAFE_MAX_RECURSION_DEPTH, "--recursion-status", SAFE_RECURSION_STATUSES])
    return cmd


def _safe_task_id(s: str) -> str:
    # keep in sync with web-vuln-common workspace_manifest.safe_task_id
    t = re.sub(r"[^a-zA-Z0-9_-]+", "_", (s or "task").strip())[:96]
    return t or "task"


def _find_latest_web_vuln_run_root(workspace_root: Path, task_id: str) -> Path | None:
    # Find the newest run directory that already has katana discovery output.
    # This allows external dirsearch to "join" the katana run for dispatcher consumption.
    tdir = workspace_root / _safe_task_id(task_id) / "web-vuln"
    if not tdir.exists() or not tdir.is_dir():
        return None

    best_run: Path | None = None
    best_mtime: float = -1.0
    try:
        for run_dir in tdir.iterdir():
            if not run_dir.is_dir():
                continue
            katana_urls = run_dir / "discovery" / "katana_urls.txt"
            if not katana_urls.exists():
                continue
            mtime = katana_urls.stat().st_mtime
            if mtime > best_mtime:
                best_mtime = mtime
                best_run = run_dir
    except Exception:
        return None
    return best_run


def _write_dirsearch_json_for_dispatcher(
    *,
    workspace_root: Path,
    task_id: str,
    run_id: str | None,
    matches: list[dict[str, Any]],
    output_dir: str | None = None,
) -> None:
    """
    写回 dispatcher 可消费的位置。

    - 路线 C：若提供 output_dir，则写入 {output_dir}/dirsearch.json
    - 否则：回退写入 {WORKSPACE_ROOT}/{task_id}/web-vuln/{run_id}/discovery/dirsearch.json
    """
    if output_dir:
        try:
            raw_od = output_dir.strip()
            dd = resolve_under_workspace(raw_od) or Path(raw_od)
            dd.mkdir(parents=True, exist_ok=True)
            results = []
            for m in matches:
                if not isinstance(m, dict):
                    continue
                url = m.get("url")
                if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                    continue
                results.append(
                    {
                        "url": url,
                        "status": m.get("status"),
                        "path": m.get("path"),
                        # support both keys in case other code expects one of them
                        "size": m.get("size"),
                        "content-length": m.get("size"),
                    }
                )
            (dd / "dirsearch.json").write_text(
                json.dumps({"results": results}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            return
        return

    # Dispatcher expects: {WORKSPACE_ROOT}/{task_id}/web-vuln/{run_id}/discovery/dirsearch.json
    rr: Path | None = None
    if run_id:
        rr = workspace_root / _safe_task_id(task_id) / "web-vuln" / run_id
        if not rr.exists():
            rr = None
    if rr is None:
        rr = _find_latest_web_vuln_run_root(workspace_root, task_id)
    if rr is None:
        return

    dd = rr / "discovery"
    try:
        dd.mkdir(parents=True, exist_ok=True)
        results = []
        for m in matches:
            if not isinstance(m, dict):
                continue
            url = m.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            results.append(
                {
                    "url": url,
                    "status": m.get("status"),
                    "path": m.get("path"),
                    # support both keys in case other code expects one of them
                    "size": m.get("size"),
                    "content-length": m.get("size"),
                }
            )
        (dd / "dirsearch.json").write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return


def main() -> int:
    started = time.perf_counter()
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    raw_target = str(params.get("url") or payload.get("target") or "").strip()
    if not raw_target:
        print(json.dumps({"status": "FAILED", "parsed_artifacts": {"error": "params.url or target required"}, "raw_stdout": "", "raw_stderr": "missing target", "duration_ms": int((time.perf_counter() - started) * 1000)}, ensure_ascii=False))
        return 1

    target = _build_target(raw_target)
    base = _wordlist_base_path()
    registry = _load_registry(base)
    keywords = _to_list(params.get("keywords")) or _legacy_tags_to_keywords(_to_list(params.get("wordlist_tags"))) or ["java", "api"]
    exclude_techs = _to_list(params.get("exclude_techs"))
    enable_recursion = bool(params.get("enable_recursion"))
    extensions = _norm_extensions(params.get("extensions"))

    wl_tmp: str | None = None
    out_tmp: str | None = None
    try:
        wl_tmp, wl_meta = _build_optimized_wordlist(base, registry, keywords, exclude_techs)
        fd, out_tmp = tempfile.mkstemp(suffix=".json", prefix="dirsearch_result_", dir="/tmp")
        os.close(fd)
        proc = subprocess.run(_build_cmd(target, wl_tmp, out_tmp, extensions, enable_recursion), capture_output=True, text=True, timeout=DIRSEARCH_PROCESS_TIMEOUT)

        parsed: dict[str, Any] = {
            "target": target,
            "matches": [],
            "applied": {
                "threads": DIRSEARCH_THREADS,
                "max_rate": DIRSEARCH_MAX_RATE,
                "request_timeout_seconds": DIRSEARCH_REQUEST_TIMEOUT,
                "process_timeout_seconds": DIRSEARCH_PROCESS_TIMEOUT,
                "extensions": extensions,
                "enable_recursion": enable_recursion,
                **wl_meta,
            },
        }
        try:
            payload_json = json.loads(Path(out_tmp).read_text(encoding="utf-8"))
            if isinstance(payload_json, dict):
                for item in payload_json.get("results", []):
                    if isinstance(item, dict):
                        parsed["matches"].append({"path": item.get("path"), "status": item.get("status"), "size": item.get("content-length"), "url": item.get("url")})
        except Exception:
            parsed["raw_preview"] = (proc.stdout or "")[:2000]

        # ---- write back for dispatcher consumption ----
        try:
            task_id = str(payload.get("task_id") or payload.get("taskId") or params.get("task_id") or params.get("taskId") or "local")
            run_id = params.get("run_id") or params.get("katana_run_id")
            workspace_root = Path(os.environ.get("WORKSPACE_ROOT", "/tmp"))
            out_dir_raw = str(params.get("output_dir") or params.get("output_discovery_dir") or "").strip()
            _write_dirsearch_json_for_dispatcher(
                workspace_root=workspace_root,
                task_id=task_id,
                run_id=str(run_id).strip() if run_id else None,
                matches=parsed.get("matches") or [],
                output_dir=out_dir_raw or None,
            )
        except Exception:
            pass

        print(json.dumps({"status": "SUCCESS" if proc.returncode == 0 else "FAILED", "parsed_artifacts": parsed, "raw_stdout": proc.stdout or "", "raw_stderr": proc.stderr or "", "duration_ms": int((time.perf_counter() - started) * 1000)}, ensure_ascii=False))
        return 0 if proc.returncode == 0 else 1
    except subprocess.TimeoutExpired as exc:
        print(json.dumps({"status": "FAILED", "parsed_artifacts": {"target": target, "error": "dirsearch process timeout", "process_timeout_seconds": DIRSEARCH_PROCESS_TIMEOUT}, "raw_stdout": exc.stdout or "", "raw_stderr": exc.stderr or "", "duration_ms": int((time.perf_counter() - started) * 1000)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "parsed_artifacts": {"target": target, "error": "unexpected error", "detail": str(exc)}, "raw_stdout": "", "raw_stderr": str(exc), "duration_ms": int((time.perf_counter() - started) * 1000)}, ensure_ascii=False))
        return 1
    finally:
        for p in (wl_tmp, out_tmp):
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
