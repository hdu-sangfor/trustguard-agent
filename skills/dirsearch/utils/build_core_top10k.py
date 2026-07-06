#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from collections import Counter
from pathlib import Path

TARGET_LINES = 10000

# Always inject high-value probing/leak paths first.
CRITICAL_SEEDS = [
    "/.env",
    "/.git/config",
    "/.svn/entries",
    "/.htaccess",
    "/web.config",
    "/.DS_Store",
    "/robots.txt",
    "/sitemap.xml",
    "/.well-known/security.txt",
    "/config.php.bak",
    "/config.save",
    "/.bash_history",
    "/WEB-INF/web.xml",
    "/META-INF/MANIFEST.MF",
    "/phpinfo.php",
    "/info.php",
    "/package.json",
    "/.npmrc",
    "/Dockerfile",
    "/docker-compose.yml",
]

FILL_SUFFIXES = [
    "",
    "/",
    ".bak",
    ".old",
    ".save",
    ".zip",
    ".tar.gz",
    ".json",
    ".yaml",
    ".yml",
]


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)


def _ensure_repo(tmp: Path, name: str, url: str) -> Path:
    path = tmp / name
    if not path.exists():
        _run(["git", "clone", "--depth", "1", url, str(path)])
    return path


def _lines_from_file(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip().replace("\r", "")
            if s:
                yield s


def _iter_sources(one: Path, sec: Path):
    yield from _lines_from_file(one / "onelistforall.txt")
    web_content = sec / "Discovery" / "Web-Content"
    if web_content.exists():
        for p in web_content.glob("*.txt"):
            yield from _lines_from_file(p)


def _is_path_like(s: str) -> bool:
    return s.startswith("/")


def _strict_ok(s: str) -> bool:
    if not _is_path_like(s):
        return False
    body = s[1:]
    if body.isdigit():
        return False
    if body.isalnum() and len(body) <= 3:
        return False
    return True


def _relaxed_ok(s: str) -> bool:
    if not _is_path_like(s):
        return False
    return not s[1:].isdigit()


def build(output: Path, tmp_dir: Path) -> int:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    one = _ensure_repo(tmp_dir, "OneListForAll", "https://github.com/six2dez/OneListForAll.git")
    sec = _ensure_repo(tmp_dir, "SecLists", "https://github.com/danielmiessler/SecLists.git")

    strict_counter: Counter[str] = Counter()
    relaxed_seen: set[str] = set()
    for item in _iter_sources(one, sec):
        if _strict_ok(item):
            strict_counter[item] += 1
        if _relaxed_ok(item):
            relaxed_seen.add(item)

    final: list[str] = []
    seen: set[str] = set()

    # 1) Critical seeds first.
    for s in CRITICAL_SEEDS:
        if s not in seen:
            final.append(s)
            seen.add(s)

    # 2) Strict high-frequency list.
    for word, _ in strict_counter.most_common():
        if word not in seen:
            final.append(word)
            seen.add(word)
        if len(final) >= TARGET_LINES:
            break

    # 3) Backfill from relaxed set if strict list not enough.
    if len(final) < TARGET_LINES:
        for word in sorted(relaxed_seen):
            if word not in seen:
                final.append(word)
                seen.add(word)
            if len(final) >= TARGET_LINES:
                break

    # 4) Deterministic synthetic backfill to always reach 10k.
    if len(final) < TARGET_LINES:
        base_candidates = [p for p in final if len(p) > 2 and p.count("/") <= 3]
        for base in base_candidates:
            clean = base.rstrip("/")
            if not clean:
                continue
            for suffix in FILL_SUFFIXES:
                candidate = f"{clean}{suffix}"
                if candidate not in seen and _relaxed_ok(candidate):
                    final.append(candidate)
                    seen.add(candidate)
                if len(final) >= TARGET_LINES:
                    break
            if len(final) >= TARGET_LINES:
                break

    if len(final) < TARGET_LINES:
        for i in range(1, TARGET_LINES + 1):
            candidate = f"/backup_{i:05d}"
            if candidate not in seen:
                final.append(candidate)
                seen.add(candidate)
            if len(final) >= TARGET_LINES:
                break

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as f:
        for w in final[:TARGET_LINES]:
            f.write(w + "\n")

    return min(len(final), TARGET_LINES)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build core_top10k wordlist from OneListForAll + SecLists.")
    parser.add_argument("--output", required=True, help="Output file path.")
    parser.add_argument("--tmp-dir", default="", help="Temp workspace path.")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    if args.tmp_dir:
        tmp_dir = Path(args.tmp_dir).resolve()
    else:
        tmp_dir = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp") / "wordlist_build"

    lines = build(output, tmp_dir)
    print(f"[+] Base 10k generated: {output} (lines={lines})")
    if lines != TARGET_LINES:
        print(f"[x] expected {TARGET_LINES} lines, got {lines}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
