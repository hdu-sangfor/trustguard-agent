from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from glob import glob


STATIC_EXCLUDED_SUBDIRS = [
    "image/",
    "images/",
    "media/",
    "css/",
    "js/",
    "fonts/",
    "assets/",
    "vendor/",
]
SAFE_RECURSION_STATUSES = "200,301,302,403"
SAFE_MAX_RECURSION_DEPTH = "2"
TAG_MAP_FILE = "tag_map.json"
ATTACK_DEFAULT_THREADS = 100
ATTACK_DEFAULT_MAX_RATE = 100
ATTACK_EXTREME_THREADS = 200
ATTACK_EXTREME_MAX_RATE = 200
SAFE_PROCESS_TIMEOUT_SECONDS = 600
SAFE_PROCESS_TIMEOUT_CAP_SECONDS = 3600
FULL_TAG_MIN_LINES = {
    "generic_full": 3000,
    "java_full": 1500,
    "api_fuzz": 500,
}
# 确定性兜底：当 full 标签命中的词条量过低时，强制补充大字典，避免“战术上有标签、弹药上却很小”。
FULL_TAG_FALLBACKS = {
    "generic_full": [
        "fuzzDicts/directoryDicts/Filenames_or_Directories_All.txt",
        "fuzzDicts/directoryDicts/top7000.txt",
    ],
    "java_full": [
        "fuzzDicts/directoryDicts/Filenames_or_Directories_All.txt",
        "fuzzDicts/uploadFileExtDicts/jsp_upload_fuzz.txt",
        "fuzzDicts/uploadFileExtDicts/tomcat_upload_fuzz.txt",
        "struts2.txt",
    ],
    "api_fuzz": [
        "fuzzDicts/apiDict/api.txt",
    ],
}


def _to_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _norm_extensions(value: object) -> str:
    items = _to_list(value)
    if not items:
        raw = str(value or "").strip()
        if not raw:
            return "php,html,js,txt"
        return raw.replace(" ", "")
    return ",".join([v.lstrip(".").replace(" ", "") for v in items if v.strip()])


def _safe_positive_int(value: object, default: int) -> int:
    try:
        n = int(value)
    except Exception:
        return default
    if n <= 0:
        return default
    return n


def _resolve_concurrency_policy(params: dict[str, object]) -> tuple[int, int]:
    """
    并发与速率策略（暴力模式）：
    - 完全忽略上层传入的 threads / rate_limit；
    - 固定使用底层默认高压参数；
    - 可通过 DIRSEARCH_ATTACK_LEVEL=extreme 切到更高档位 200/200。
    """
    level = (os.getenv("DIRSEARCH_ATTACK_LEVEL") or "").strip().lower()
    if level == "extreme":
        return ATTACK_EXTREME_THREADS, ATTACK_EXTREME_MAX_RATE
    return ATTACK_DEFAULT_THREADS, ATTACK_DEFAULT_MAX_RATE


def _load_wordlist_by_tag(tag: str) -> Path | None:
    base = Path(__file__).resolve().parent.parent / "wordlists"
    safe_tag = "".join(ch for ch in (tag or "").lower() if ch.isalnum() or ch in ("_", "-"))
    if not safe_tag:
        return None
    path = base / f"{safe_tag}.txt"
    return path if path.exists() else None


def _resolve_wordlist_entry(entry: str) -> list[Path]:
    base = Path(__file__).resolve().parent.parent / "wordlists"
    e = str(entry or "").strip().replace("\\", "/")
    if not e:
        return []
    if "*" in e or "?" in e:
        out: list[Path] = []
        for fp in glob(str(base / e), recursive=True):
            p = Path(fp)
            if p.exists() and p.is_file():
                out.append(p)
        return out
    p = base / e
    if p.exists() and p.is_file():
        return [p]
    if not e.lower().endswith(".txt"):
        p2 = base / f"{e}.txt"
        if p2.exists() and p2.is_file():
            return [p2]
    return []


def _load_tag_map() -> dict[str, list[str]]:
    base = Path(__file__).resolve().parent.parent / "wordlists"
    p = base / TAG_MAP_FILE
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        kk = str(k or "").strip().lower()
        if not kk:
            continue
        vals = _to_list(v)
        if vals:
            out[kk] = vals
    return out


def _merge_wordlists(tag_paths: list[Path], default_wordlist: str) -> tuple[str, str | None]:
    if not tag_paths:
        return default_wordlist, None
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as tmp:
        merged_path = tmp.name
        seen = set()
        for wl in tag_paths:
            try:
                for raw_line in wl.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line in seen:
                        continue
                    seen.add(line)
                    tmp.write(line + "\n")
            except Exception:
                continue
        if not seen:
            return default_wordlist, merged_path
    return merged_path, merged_path


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for line in f if line.strip() and not line.lstrip().startswith("#"))
    except Exception:
        return 0


def _build_target(target: str) -> str:
    if target.startswith(("http://", "https://")):
        return target
    return f"http://{target}"


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
    timeout = int(params.get("timeout") or 3)
    timeout = max(1, min(timeout, 10))
    # 仅作为 dirsearch HTTP 请求级超时；不要误用为 subprocess 进程生存时间。
    process_timeout = _safe_positive_int(
        params.get("process_timeout_seconds"),
        SAFE_PROCESS_TIMEOUT_SECONDS,
    )
    process_timeout = min(process_timeout, SAFE_PROCESS_TIMEOUT_CAP_SECONDS)
    extensions = _norm_extensions(params.get("extensions"))

    # Find first available default wordlist
    common_wordlists = [
        "/usr/local/dirsearch/db/dicc.txt",
        "/usr/local/dirsearch/db/categories/common.txt",
        "/usr/local/dirsearch/db/categories/web.txt",
        "/usr/share/wordlists/dirb/common.txt",
        "/usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt",
    ]
    
    wl_path = None
    for wordlist in common_wordlists:
        if os.path.exists(wordlist):
            wl_path = wordlist
            break
    
    if not wl_path:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "no default wordlist found", "tried_paths": common_wordlists},
            "raw_stdout": "",
            "raw_stderr": "no default wordlist file found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    wordlist_tags = _to_list(params.get("wordlist_tags"))
    tag_map = _load_tag_map()
    tagged_wordlists: list[Path] = []
    missing_tags: list[str] = []
    resolved_tag_files: dict[str, list[str]] = {}
    for tag in wordlist_tags:
        aliases = tag_map.get(tag.lower(), [f"{tag}.txt"])
        found_for_tag: list[Path] = []
        for alias in aliases:
            found_for_tag.extend(_resolve_wordlist_entry(str(alias)))
        if not found_for_tag:
            # 保持向后兼容：tag_map 未命中时仍尝试 `${tag}.txt`
            p = _load_wordlist_by_tag(tag)
            if p is not None:
                found_for_tag.append(p)
        if not found_for_tag:
            missing_tags.append(tag)
            continue
        resolved_tag_files[tag] = [fp.name for fp in found_for_tag]
        tagged_wordlists.extend(found_for_tag)

    # Full 标签兜底：若命中的总词条量太小，补充确定性大字典，避免误退化到“小样本”扫描。
    full_tag_line_counts: dict[str, int] = {}
    for tag in wordlist_tags:
        t = tag.lower()
        min_lines = FULL_TAG_MIN_LINES.get(t)
        if min_lines is None:
            continue
        current = [p for p in tagged_wordlists if p.is_file()]
        line_count = sum(_count_lines(p) for p in current)
        full_tag_line_counts[t] = line_count
        if line_count >= min_lines:
            continue
        for fb in FULL_TAG_FALLBACKS.get(t, []):
            for p in _resolve_wordlist_entry(fb):
                if p not in tagged_wordlists:
                    tagged_wordlists.append(p)
                    resolved_tag_files.setdefault(tag, []).append(p.name)

    wl_path, merged_wordlist_tmp = _merge_wordlists(tagged_wordlists, wl_path)

    thread_count, max_rate = _resolve_concurrency_policy(params)

    output_file = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json")
    output_file_path = output_file.name
    output_file.close()

    cmd = [
        "dirsearch",
        "-u", target,
        "-w", wl_path,
        "-t", str(thread_count),
        "-e", extensions,
        "-O", "json",
        "-o", output_file_path,
        "-q",
        "--timeout", str(timeout),
        "--retries", "1",
        "--random-agent",
        #"--max-rate", str(max_rate),
        "--exclude-subdirs", ",".join(STATIC_EXCLUDED_SUBDIRS),
    ]

    if bool(params.get("enable_recursion")):
        cmd.extend([
            "-r",
            "-R", SAFE_MAX_RECURSION_DEPTH,
            "--recursion-status", SAFE_RECURSION_STATUSES,
        ])

    fuzz_prefixes = _to_list(params.get("fuzz_prefixes"))
    if fuzz_prefixes:
        cmd.extend(["--prefixes", ",".join(fuzz_prefixes)])

    fuzz_suffixes = _to_list(params.get("fuzz_suffixes"))
    if fuzz_suffixes:
        cmd.extend(["--suffixes", ",".join(fuzz_suffixes)])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )
    except FileNotFoundError:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "dirsearch not found"},
            "raw_stdout": "",
            "raw_stderr": "dirsearch executable not found",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    except subprocess.TimeoutExpired as exc:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {
                "target": target,
                "error": "dirsearch process timeout",
                "process_timeout_seconds": process_timeout,
                "request_timeout_seconds": timeout,
            },
            "raw_stdout": exc.stdout or "",
            "raw_stderr": exc.stderr or "",
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    finally:
        if merged_wordlist_tmp:
            try:
                os.unlink(merged_wordlist_tmp)
            except Exception:
                pass
        
    parsed = {
        "target": target,
        "matches": [],
        "applied": {
            "timeout": timeout,
            "process_timeout_seconds": process_timeout,
            "retries": 1,
            "random_agent": True,
            "exclude_subdirs": STATIC_EXCLUDED_SUBDIRS,
            "threads": thread_count,
            "max_rate": max_rate,
            "extensions": extensions,
            "enable_recursion": bool(params.get("enable_recursion")),
            "wordlist_tags": wordlist_tags,
            "resolved_tag_files": resolved_tag_files,
            "full_tag_line_counts": full_tag_line_counts,
        },
    }
    if missing_tags:
        parsed["missing_wordlist_tags"] = missing_tags
    try:
        with open(output_file_path, "r", encoding="utf-8") as f:
            output_data = json.load(f)
            if isinstance(output_data, dict) and "results" in output_data:
                for item in output_data.get("results", []):
                    if isinstance(item, dict):
                        parsed["matches"].append({
                            "path": item.get("path"),
                            "status": item.get("status"),
                            "size": item.get("content-length"),
                            "url": item.get("url"),
                        })
    except Exception:
        parsed["raw_preview"] = (proc.stdout or "")[:2000]
    finally:
        try:
            os.unlink(output_file_path)
        except Exception:
            pass

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