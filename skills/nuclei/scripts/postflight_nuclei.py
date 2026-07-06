from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def extract_raw_preview_for_exploit(result_path: Path, limit: int = 1) -> list[str]:
    if not result_path.exists():
        return []
    previews: list[str] = []
    try:
        for line in result_path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                item = json.loads(s)
            except Exception:
                continue
            extracted = item.get("extracted-results")
            if isinstance(extracted, list):
                for v in extracted:
                    vs = str(v or "").strip()
                    if vs:
                        previews.append(vs[:500])
                        if len(previews) >= limit:
                            return previews
            matched_at = str(item.get("matched-at") or "").strip()
            if matched_at:
                previews.append(matched_at[:500])
                if len(previews) >= limit:
                    return previews
    except Exception:
        return previews
    return previews[:limit]


def enrich_postflight_artifacts(
    artifacts: dict[str, Any],
    *,
    mode: str,
    result_path: Path,
) -> dict[str, Any]:
    out = dict(artifacts or {})
    if (mode or "").strip().lower() == "exploit":
        previews = extract_raw_preview_for_exploit(result_path, limit=3)
        if previews:
            out["raw_preview"] = previews
    return out
