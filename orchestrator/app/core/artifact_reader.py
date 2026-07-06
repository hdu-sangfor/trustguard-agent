from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os


WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/data/workspace"))
REF_PREFIX = "wsref:"


def _resolve_ref(artifact_ref: str) -> Path | None:
    ref = (artifact_ref or "").strip()
    if not ref:
        return None
    p = Path(ref)
    if p.is_absolute():
        try:
            p.resolve().relative_to(WORKSPACE_ROOT.resolve())
            return p.resolve()
        except Exception:
            return None
    if not ref.startswith(REF_PREFIX):
        return None
    rel = ref[len(REF_PREFIX):].strip().replace("\\", "/")
    parts = [x for x in rel.split("/") if x and x not in (".", "..")]
    if len(parts) != 3:
        return None
    return (WORKSPACE_ROOT / parts[0] / "artifacts" / parts[1] / parts[2]).resolve()


def load_parsed_from_artifact_ref(artifact_ref: str) -> dict[str, Any]:
    if not artifact_ref:
        return {}
    base = _resolve_ref(artifact_ref)
    if base is None:
        return {}
    parsed_file = base / "parsed.json"
    if not parsed_file.exists():
        return {}
    try:
        data = json.loads(parsed_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_event_id_from_artifact_ref(artifact_ref: str) -> str:
    if not artifact_ref:
        return ""
    base = _resolve_ref(artifact_ref)
    if base is None:
        return ""
    folder_name = base.name
    if "_" in folder_name:
        return folder_name.split("_", 1)[0]
    meta_file = base / "meta.json"
    if not meta_file.exists():
        return ""
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        value = data.get("event_id")
        return str(value) if value is not None else ""
    except Exception:
        return ""

