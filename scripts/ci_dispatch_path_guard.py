"""
r4f-b 终局承接（部分）：CI 静态门禁，避免新增派发分支漏接 compile_plan_item。

本脚本不依赖第三方库，仅做最小字符串不变量检查：
- PlanItem 编译入口：`plan_execution_dispatch.py` 必须调用 `compile_plan_item(`。
- 编译器函数调用点收敛：除定义文件与测试外，`compile_plan_item(` 只能出现在 `plan_execution_dispatch.py`。

注意：这是 guard rail，不替代单测；真正的行为契约仍由 pytest 覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _fail(msg: str) -> int:
    print(f"[dispatch-guard] FAIL: {msg}")
    return 2


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    root = Path(__file__).resolve().parent.parent

    orch_core = root / "orchestrator" / "app" / "core"
    if not orch_core.is_dir():
        return _fail(f"missing orchestrator core dir: {orch_core}")

    plan_exec = orch_core / "plan_execution_dispatch.py"
    if not plan_exec.is_file():
        return _fail("missing plan_execution_dispatch.py")

    plan_text = _read_text(plan_exec)
    if "compile_plan_item(" not in plan_text:
        return _fail("plan_execution_dispatch.py must call compile_plan_item(...)")

    # 收敛调用点：不应在其他生产模块里直接调用 compile_plan_item。
    # 允许：定义文件 instruction_compiler.py；测试目录 tests/。
    offenders: list[str] = []
    for p in orch_core.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel.endswith("/instruction_compiler.py"):
            continue
        if rel.endswith("/plan_execution_dispatch.py"):
            continue
        text = _read_text(p)
        if "compile_plan_item(" in text:
            offenders.append(rel)

    if offenders:
        offenders_s = ", ".join(sorted(offenders))
        return _fail(f"compile_plan_item(...) call sites must be centralized; found in: {offenders_s}")

    print("[dispatch-guard] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

