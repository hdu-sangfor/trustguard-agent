"""r4f-e 承接：编排器与 Executor（含 MQ Worker）侧 `context` 白名单 frozenset 必须一致。"""
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

_ROOT = REPO_ROOT
_ORCH = str(_ROOT / "orchestrator")
_EXEC = str(_ROOT / "executor")


def _purge_app_modules() -> None:
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_execution_context_allowed_keys_match_across_services() -> None:
    _purge_app_modules()
    sys.path.insert(0, _ORCH)
    try:
        from app.core.agent_context import (  # type: ignore[import]
            EXECUTION_CONTEXT_ALLOWED_KEYS as orch_keys,
        )
    finally:
        sys.path.remove(_ORCH)
    _purge_app_modules()
    sys.path.insert(0, _EXEC)
    try:
        from app.core.execution_context_allowlist import (  # type: ignore[import]
            EXECUTION_CONTEXT_ALLOWED_KEYS as exec_keys,
        )
    finally:
        sys.path.remove(_EXEC)
    _purge_app_modules()
    assert orch_keys == exec_keys
    assert "plan_id" in orch_keys
    assert "todo_id" in exec_keys
