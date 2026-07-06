#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)


def main() -> int:
    repo = Path(__file__).resolve().parents[3]
    base = repo / "skills" / "dirsearch" / "wordlists" / "v1"
    dict_open = base / "dict_open"
    registry_path = base / "registry.json"

    tmp = Path.home() / "AppData" / "Local" / "Temp" / "wordlist_sync_full"
    one = tmp / "OneListForAll"
    tmp.mkdir(parents=True, exist_ok=True)
    if not one.exists():
        run(["git", "clone", "--depth", "1", "https://github.com/six2dez/OneListForAll.git", str(one)])

    src_dict = one / "dict"
    if not src_dict.exists():
        raise RuntimeError("OneListForAll/dict not found")

    dict_open.mkdir(parents=True, exist_ok=True)
    for p in dict_open.glob("*.txt"):
        p.unlink()

    copied: list[str] = []
    for p in sorted(src_dict.glob("*_short.txt")):
        dst = dict_open / p.name
        shutil.copy2(p, dst)
        copied.append(dst.name)

    if not copied:
        raise RuntimeError("No *_short.txt found to sync")

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    mappings: dict[str, list[str]] = {}

    for name in copied:
        key = name[: -len("_short.txt")]
        mappings[key] = [f"dict_open/{name}"]

    mappings["java"] = ["dict_open/java_short.txt", "seeds/struts2.txt"] if "java_short.txt" in copied else ["seeds/struts2.txt"]
    mappings["api"] = ["dict_open/api_short.txt"] if "api_short.txt" in copied else []
    admin_chain: list[str] = []
    if "admin_short.txt" in copied:
        admin_chain.append("dict_open/admin_short.txt")
    admin_chain.append("dict_private/internal_admin.txt")
    mappings["admin"] = admin_chain

    mappings = {k: v for k, v in mappings.items() if v}
    registry["mappings"] = dict(sorted(mappings.items(), key=lambda kv: kv[0]))
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"COPIED={len(copied)}")
    print(f"MAPPINGS={len(registry['mappings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
