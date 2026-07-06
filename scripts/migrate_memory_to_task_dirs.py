#!/usr/bin/env python3
"""
将 workspace 根目录下的 memory-*-parsed.json 迁入对应任务的 memory/ 子目录。

编排器自本迁移后，新写入路径为：
  {WORKSPACE_ROOT}/{safe_task}/memory/memory-{safe_task}-{eid}-parsed.json

用法（仓库根目录）:
  python scripts/migrate_memory_to_task_dirs.py data/evidence-workspace
  python scripts/migrate_memory_to_task_dirs.py data/evidence-workspace --dry-run
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate legacy memory JSON from workspace root into task dirs.")
    ap.add_argument(
        "workspace",
        nargs="?",
        default="data/evidence-workspace",
        help="Workspace root (Evidence volume root, e.g. data/evidence-workspace)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print actions only, do not move files")
    args = ap.parse_args()
    root = Path(args.workspace).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 1

    task_dirs = [d for d in root.iterdir() if d.is_dir()]
    # 优先匹配更长目录名，避免 task-abc 与 task-abc-def 歧义（项目中 task_id 一般为唯一 UUID 段）
    task_dirs.sort(key=lambda p: len(p.name), reverse=True)

    moved = 0
    skipped = 0
    for src in sorted(root.glob("memory-*-parsed.json")):
        if not src.is_file():
            continue
        dest_parent: Path | None = None
        for d in task_dirs:
            prefix = f"memory-{d.name}-"
            if src.name.startswith(prefix):
                dest_parent = d / "memory"
                break
        if dest_parent is None:
            print(f"[skip] no matching task dir for: {src.name}")
            skipped += 1
            continue
        dest = dest_parent / src.name
        if dest.resolve() == src.resolve():
            continue
        if dest.exists():
            print(f"[skip] destination exists: {dest}")
            skipped += 1
            continue
        print(f"{'[dry-run] ' if args.dry_run else ''}move {src.name} -> {dest}")
        if not args.dry_run:
            dest_parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        moved += 1

    print(f"Done. moved={moved} skipped={skipped} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
