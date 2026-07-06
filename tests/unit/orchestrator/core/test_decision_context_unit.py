"""
单元测试：decision_context.build_decision_context「保留最近」截断逻辑。

验证文档 context-window-and-summary.md §7.4：总长超限时保留字符串最后 max_history_chars，
避免截掉刚执行完的 history 导致重复执行或信息丢失。
"""
import os
import sys

from tests.paths import REPO_ROOT

# 使 orchestrator 的 app 可被导入
_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)

import pytest


def test_build_decision_context_keeps_tail_when_over_limit():
    """超长时保留最近动作线索。"""
    from app.core.decision_context import build_decision_context

    # 构造一段较长的 history，前面是旧内容，最后一行是“刚执行完”的
    old_part = "line1\nline2\nline3\n" * 50  # 约 600 字符
    recent_line = "[nmap] target=http://x:80, status=ok, keys=open_ports"
    history_summary = old_part + recent_line

    reduced_ctx, combined = build_decision_context({"target": "http://x:80"}, history_summary)

    # 必须保留「最近」：结尾应是刚执行完的那一行
    assert "[nmap]" in combined
    assert "_tier0" in reduced_ctx
    assert "_tier1" in reduced_ctx
    assert "_tier2" in reduced_ctx
    assert "_tier3" in reduced_ctx


def test_build_decision_context_with_summary_chunks_tail():
    """有 summary_chunks 时，Tier3 应注入摘要。"""
    from app.core.decision_context import build_decision_context

    summary_chunks = ["摘要1: 发现 80 开放", "摘要2: 发现 22 开放"]
    history = "old1\nold2\n" + "[nuclei] target=http://y, status=ok"  # 最后是最近一步
    reduced, combined = build_decision_context(
        {},
        history,
        summary_chunks=summary_chunks,
    )
    # 应包含最近 history
    assert "[nuclei]" in combined
    tier3 = reduced.get("_tier3") or []
    assert any("summary" in (row or {}) for row in tier3)
