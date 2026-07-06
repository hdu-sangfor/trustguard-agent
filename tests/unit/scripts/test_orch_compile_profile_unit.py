"""r4f-c profile 脚本：基础输出契约。"""

from __future__ import annotations

import sys
from pathlib import Path
from tests.paths import REPO_ROOT

_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from orch_compile_profile import run_profile


def test_run_profile_returns_expected_shape():
    out = run_profile(runs=3, refs_count=1, body_size=16, cache_enabled=True)
    assert out["runs"] == 3
    assert out["refs_count"] == 1
    assert out["cache_enabled"] is True
    assert isinstance(out["avg_ms"], float)
    assert isinstance(out["p95_ms"], float)
    assert isinstance(out["max_ms"], float)
    assert out["ok_count"] == 3

