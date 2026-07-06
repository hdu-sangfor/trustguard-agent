"""
单测：P3 SSE 增量工件流端点（/v1/orchestrator/tasks/{id}/trace/stream）。

v1-a251：
- ENV 解析（poll interval / max seconds）
- 404 任务不存在
- 活跃任务：仅推送新增工件（游标机制）
- 非活跃任务（不在内存）：一次性推送后关闭
- 脱敏：Bearer token 被 [REDACTED]
- 心跳注释行格式正确
- timeout 事件
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from tests.paths import REPO_ROOT
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = REPO_ROOT
sys.path.insert(0, str(_ROOT / "orchestrator"))

from tests.orchestrator_test_env import prepare_orchestrator_app_import  # noqa: E402

prepare_orchestrator_app_import()


# ---------------------------------------------------------------------------
# ENV 解析单测
# ---------------------------------------------------------------------------


class TestTraceStreamEnvParsing:
    def test_poll_interval_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", raising=False)
        from app.main import _trace_stream_poll_interval

        assert _trace_stream_poll_interval() == 1.0

    def test_poll_interval_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "2.5")
        from app.main import _trace_stream_poll_interval

        assert _trace_stream_poll_interval() == 2.5

    def test_poll_interval_clamped_low(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "0.001")
        from app.main import _trace_stream_poll_interval

        assert _trace_stream_poll_interval() == 0.1

    def test_poll_interval_clamped_high(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "999")
        from app.main import _trace_stream_poll_interval

        assert _trace_stream_poll_interval() == 60.0

    def test_poll_interval_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "not-a-float")
        from app.main import _trace_stream_poll_interval

        assert _trace_stream_poll_interval() == 1.0

    def test_max_seconds_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_TRACE_STREAM_MAX_SECONDS", raising=False)
        from app.main import _trace_stream_max_seconds

        assert _trace_stream_max_seconds() == 300.0

    def test_max_seconds_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "60")
        from app.main import _trace_stream_max_seconds

        assert _trace_stream_max_seconds() == 60.0

    def test_max_seconds_clamped_low(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "1")
        from app.main import _trace_stream_max_seconds

        assert _trace_stream_max_seconds() == 5.0

    def test_max_seconds_clamped_high(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "99999")
        from app.main import _trace_stream_max_seconds

        assert _trace_stream_max_seconds() == 3600.0

    def test_max_seconds_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "abc")
        from app.main import _trace_stream_max_seconds

        assert _trace_stream_max_seconds() == 300.0


# ---------------------------------------------------------------------------
# 辅助：收集 SSE 事件生成器输出
# ---------------------------------------------------------------------------

async def _collect_sse_chunks(generator, max_chunks: int = 100) -> list[str]:
    """消费异步生成器，收集全部 chunk，上限 max_chunks 防止死循环。"""
    chunks = []
    count = 0
    async for chunk in generator:
        chunks.append(chunk)
        count += 1
        if count >= max_chunks:
            break
    return chunks


def _parse_sse_data_events(chunks: list[str]) -> list[dict[str, Any]]:
    """从 SSE chunks 中提取 data: 行并 JSON 解析。"""
    events = []
    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    return events


def _sse_event_types(chunks: list[str]) -> list[str]:
    """提取 event: 行值。"""
    types = []
    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith("event: "):
                types.append(line[7:])
    return types


def _has_heartbeat(chunks: list[str]) -> bool:
    return any(": heartbeat" in c for c in chunks)


# ---------------------------------------------------------------------------
# 生成器核心逻辑单测（直接构造模拟上下文，绕过 HTTP 层）
# ---------------------------------------------------------------------------


class TestStreamGeneratorNonLiveTask:
    """非活跃任务（不在 _TASKS 内存）：推送完毕即关闭。"""

    @pytest.mark.asyncio
    async def test_non_live_empty_artifacts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 checkpoint → 零条工件 + done 事件。"""
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "0.1")
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "30")

        import app.main as m

        # 确保 task_id 不在内存
        tid = "t-stream-non-live-empty"
        m._TASKS.pop(tid, None)

        with patch.object(m, "load_checkpoint_remote", new=AsyncMock(return_value=None)):
            with patch.object(
                m._TASK_STORE, "get_task", new=AsyncMock(return_value=MagicMock())
            ):
                from fastapi import HTTPException as FE

                # 直接调用端点获取 StreamingResponse
                resp = await m.stream_task_incremental_artifacts(tid)
                chunks = await _collect_sse_chunks(resp.body_iterator)

        event_types = _sse_event_types(chunks)
        assert "done" in event_types

    @pytest.mark.asyncio
    async def test_non_live_with_artifacts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """有 checkpoint 含工件 → 推送全部工件后 done。"""
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "0.1")
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "30")

        import app.main as m

        tid = "t-stream-non-live-arts"
        m._TASKS.pop(tid, None)

        artifacts = [
            {"kind": "INCREMENTAL_ARTIFACT", "summary": "found open port 80", "tool": "nmap"},
            {"kind": "INCREMENTAL_ARTIFACT", "summary": "found XSS", "tool": "httpx"},
        ]
        ck = {"target_context": {"_tactical_incremental_artifacts": artifacts}}

        with patch.object(m, "load_checkpoint_remote", new=AsyncMock(return_value=ck)):
            with patch.object(
                m._TASK_STORE, "get_task", new=AsyncMock(return_value=MagicMock())
            ):
                resp = await m.stream_task_incremental_artifacts(tid)
                chunks = await _collect_sse_chunks(resp.body_iterator)

        events = _parse_sse_data_events(chunks)
        # 两条工件均应出现
        summaries = [e.get("summary") for e in events]
        assert "found open port 80" in summaries
        assert "found XSS" in summaries
        # done 事件存在
        assert "done" in _sse_event_types(chunks)


class TestStreamGeneratorLiveTask:
    """活跃任务（在 _TASKS 内存）：增量游标推送。"""

    @pytest.mark.asyncio
    async def test_live_task_streams_incremental(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """模拟任务在两次 poll 间新增工件，游标只推新条目；game plan：
        step=1 heartbeat → append item-A at step=1 →
        step=2 data(item-A) → step=3 heartbeat → append item-B at step=3 →
        step=4 data(item-B) → step=5 heartbeat → pop _TASKS at step=5 → done.
        """
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "0.05")
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "10")

        import app.main as m

        tid = "t-stream-live-incremental"

        # 构造 mock TaskState
        mock_state = MagicMock()
        artifacts_list: list[dict] = []
        mock_state.target_context = {"_tactical_incremental_artifacts": artifacts_list}
        m._TASKS[tid] = mock_state

        resp = await m.stream_task_incremental_artifacts(tid)

        collected: list[str] = []

        async def _drive():
            step = 0
            async for chunk in resp.body_iterator:
                collected.append(chunk)
                step += 1
                if step == 1:
                    # 第 1 个心跳后追加 item-A；下次 loop 读到
                    artifacts_list.append(
                        {"kind": "INCREMENTAL_ARTIFACT", "summary": "item-A finding", "tool": "nmap"}
                    )
                if step == 3:
                    # 收到 item-A data + 心跳后追加 item-B
                    artifacts_list.append(
                        {"kind": "INCREMENTAL_ARTIFACT", "summary": "item-B finding", "tool": "httpx"}
                    )
                if step == 5:
                    # 收到 item-B data + 心跳后退出：把任务移出内存，让生成器走非活跃分支 done
                    m._TASKS.pop(tid, None)
                if step >= 20:
                    break

        with patch.object(m, "load_checkpoint_remote", new=AsyncMock(return_value=None)):
            with patch.object(
                m._TASK_STORE, "get_task", new=AsyncMock(return_value=MagicMock())
            ):
                await _drive()

        events = _parse_sse_data_events(collected)
        summaries = [e.get("summary") for e in events]
        assert "item-A finding" in summaries
        assert "item-B finding" in summaries
        # 游标机制确保不重复发送
        assert summaries.count("item-A finding") == 1

        m._TASKS.pop(tid, None)  # 清理


class TestStreamGeneratorHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_present_for_live_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "0.05")
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "10")

        import app.main as m

        tid = "t-stream-heartbeat"
        mock_state = MagicMock()
        mock_state.target_context = {"_tactical_incremental_artifacts": []}
        m._TASKS[tid] = mock_state

        resp = await m.stream_task_incremental_artifacts(tid)
        collected: list[str] = []

        with patch.object(m, "load_checkpoint_remote", new=AsyncMock(return_value=None)):
            with patch.object(m._TASK_STORE, "get_task", new=AsyncMock(return_value=MagicMock())):
                step = 0
                async for chunk in resp.body_iterator:
                    collected.append(chunk)
                    step += 1
                    if step >= 3:
                        m._TASKS.pop(tid, None)
                    if step >= 10:
                        break

        assert _has_heartbeat(collected)
        m._TASKS.pop(tid, None)


class TestStream404:
    @pytest.mark.asyncio
    async def test_task_not_found_raises_404(self) -> None:
        import app.main as m
        from fastapi import HTTPException

        tid = "t-stream-nonexistent-xyz"
        m._TASKS.pop(tid, None)

        with patch.object(m._TASK_STORE, "get_task", new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await m.stream_task_incremental_artifacts(tid)
        assert exc_info.value.status_code == 404


class TestStreamRedaction:
    @pytest.mark.asyncio
    async def test_bearer_token_redacted_in_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_TRACE_STREAM_POLL_INTERVAL_SECONDS", "0.1")
        monkeypatch.setenv("ORCH_TRACE_STREAM_MAX_SECONDS", "30")
        monkeypatch.setenv("ORCH_TRACE_REDACT_SENSITIVE", "true")

        import app.main as m

        tid = "t-stream-redact"
        m._TASKS.pop(tid, None)

        sensitive_artifact = {
            "kind": "INCREMENTAL_ARTIFACT",
            "summary": "Authorization: Bearer SECRET_TOKEN_XYZ123",
            "tool": "httpx",
        }
        ck = {"target_context": {"_tactical_incremental_artifacts": [sensitive_artifact]}}

        with patch.object(m, "load_checkpoint_remote", new=AsyncMock(return_value=ck)):
            with patch.object(
                m._TASK_STORE, "get_task", new=AsyncMock(return_value=MagicMock())
            ):
                resp = await m.stream_task_incremental_artifacts(tid)
                chunks = await _collect_sse_chunks(resp.body_iterator)

        raw = "".join(chunks)
        assert "SECRET_TOKEN_XYZ123" not in raw
