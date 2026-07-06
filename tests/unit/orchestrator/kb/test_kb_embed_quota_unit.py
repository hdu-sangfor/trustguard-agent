"""nf-perf-quotas-embed-chunk：KB 嵌入配额（进程内滑动窗口）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH = str(REPO_ROOT / "orchestrator")
if _ORCH not in sys.path:
    sys.path.insert(0, _ORCH)


@pytest.mark.asyncio
async def test_embed_quota_reject_mode(monkeypatch):
    monkeypatch.setenv("KB_EMBED_MAX_CALLS_PER_SECOND", "1")
    monkeypatch.setenv("KB_EMBED_QUOTA_ON_EXCEED", "reject")
    import importlib

    import app.kb_embed_quota as qmod
    import app.kb_embed_quota_metrics as mmod

    importlib.reload(mmod)
    importlib.reload(qmod)

    from app.kb_embed_quota import EmbedQuotaExceeded, acquire_kb_embed_slot

    await acquire_kb_embed_slot("s1")
    with pytest.raises(EmbedQuotaExceeded):
        await acquire_kb_embed_slot("s1")


@pytest.mark.asyncio
async def test_embed_quota_per_scope_minute(monkeypatch):
    monkeypatch.setenv("KB_EMBED_MAX_CALLS_PER_SECOND", "0")
    monkeypatch.setenv("KB_EMBED_MAX_CALLS_PER_MINUTE_PER_SCOPE", "2")
    monkeypatch.setenv("KB_EMBED_QUOTA_ON_EXCEED", "reject")
    import importlib

    import app.kb_embed_quota as qmod
    import app.kb_embed_quota_metrics as mmod

    importlib.reload(mmod)
    importlib.reload(qmod)

    from app.kb_embed_quota import EmbedQuotaExceeded, acquire_kb_embed_slot

    await acquire_kb_embed_slot("tenant-a")
    await acquire_kb_embed_slot("tenant-a")
    with pytest.raises(EmbedQuotaExceeded):
        await acquire_kb_embed_slot("tenant-a")
    await acquire_kb_embed_slot("tenant-b")


@pytest.mark.asyncio
async def test_embed_quota_wait_releases_after_window(monkeypatch):
    monkeypatch.setenv("KB_EMBED_MAX_CALLS_PER_SECOND", "2")
    monkeypatch.setenv("KB_EMBED_QUOTA_ON_EXCEED", "wait")
    monkeypatch.setenv("KB_EMBED_QUOTA_WAIT_MS_MAX", "5000")
    import importlib

    import app.kb_embed_quota as qmod
    import app.kb_embed_quota_metrics as mmod

    importlib.reload(mmod)
    importlib.reload(qmod)

    from app.kb_embed_quota import acquire_kb_embed_slot

    await acquire_kb_embed_slot(None)
    await acquire_kb_embed_slot(None)
    pending = asyncio.create_task(acquire_kb_embed_slot(None))
    await asyncio.sleep(0.05)
    assert not pending.done()
    await asyncio.sleep(1.05)
    await asyncio.wait_for(pending, timeout=3.0)
