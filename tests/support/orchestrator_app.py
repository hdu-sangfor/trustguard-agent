"""
编排器 Python 单测：临时从 sys.path 移除非编排器 `app` 根（executor），
避免与 orchestrator 顶层包名冲突。
"""
from __future__ import annotations

import contextlib
import sys
from typing import Iterator

from tests.support.paths import EVIDENCE_ROOT, EXECUTOR_ROOT, GATEWAY_ROOT, ORCHESTRATOR_ROOT

_EXEC = str(EXECUTOR_ROOT)
_ORCH = str(ORCHESTRATOR_ROOT)
_EVIDENCE = str(EVIDENCE_ROOT)
_GATEWAY = str(GATEWAY_ROOT)
_SERVICE_ROOTS = (_EXEC, _ORCH, _EVIDENCE, _GATEWAY)


def _loaded_app_is_orchestrator() -> bool:
    m = sys.modules.get("app")
    fp = getattr(m, "__file__", None) if m is not None else None
    paths = [str(fp)] if fp else []
    paths.extend(str(p) for p in getattr(m, "__path__", []) or [])
    return any("orchestrator" in p.replace("\\", "/") for p in paths)


def prepare_orchestrator_app_import() -> None:
    """在模块顶层 `from app...` 之前调用：若当前 `app` 非编排器包则调整后优先解析 orchestrator。"""
    for p in _SERVICE_ROOTS:
        while p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, _ORCH)
    if not _loaded_app_is_orchestrator():
        for k in list(sys.modules):
            if k == "app" or k.startswith("app."):
                del sys.modules[k]


@contextlib.contextmanager
def orchestrator_sys_path_isolated() -> Iterator[None]:
    saved = list(sys.path)
    try:
        for k in list(sys.modules):
            if k == "app" or k.startswith("app."):
                del sys.modules[k]
        for p in (_EXEC, _EVIDENCE, _GATEWAY):
            while p in sys.path:
                sys.path.remove(p)
        sys.path.insert(0, _ORCH)
        yield
    finally:
        for k in list(sys.modules):
            if k == "app" or k.startswith("app."):
                del sys.modules[k]
        sys.path[:] = saved
