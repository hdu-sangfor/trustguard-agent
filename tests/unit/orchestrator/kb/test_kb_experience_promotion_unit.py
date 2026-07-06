"""kb-r4b：经验 pending→proven 异步晋升（召回计数 + 后台 sweep）。"""
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH_ROOT = REPO_ROOT / "orchestrator"
if str(_ORCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_ORCH_ROOT))


@pytest.mark.asyncio
async def test_note_skipped_when_interval_zero(monkeypatch):
    import app.kb_experience_promotion as promo

    monkeypatch.setenv("KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS", "0")
    promo.reset_promotion_state_for_tests()
    await promo.note_kb_experience_surfaces("t1", ["p1", "p2"])
    assert promo._counts == {}


@pytest.mark.asyncio
async def test_note_dedupes_same_tick(monkeypatch):
    import app.kb_experience_promotion as promo

    monkeypatch.setenv("KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS", "60")
    promo.reset_promotion_state_for_tests()
    await promo.note_kb_experience_surfaces("t1", ["a", "a", "a"])
    assert promo._counts == {"a": 1}
    assert promo._last_task.get("a") == "t1"


@pytest.mark.asyncio
async def test_note_accumulates_across_ticks(monkeypatch):
    import app.kb_experience_promotion as promo

    monkeypatch.setenv("KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("KB_EXPERIENCE_PROMOTE_RECALLS", "2")
    promo.reset_promotion_state_for_tests()
    await promo.note_kb_experience_surfaces("t1", ["x"])
    await promo.note_kb_experience_surfaces("t2", ["x"])
    assert promo._counts["x"] == 2
    assert promo._last_task["x"] == "t2"


@pytest.mark.asyncio
async def test_sweep_invokes_promote_and_clears_counter(monkeypatch):
    import app.kb_experience_promotion as promo

    monkeypatch.setenv("KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("KB_EXPERIENCE_PROMOTE_RECALLS", "1")
    promo.reset_promotion_state_for_tests()
    calls: list[tuple[str, str, int, str]] = []

    async def _fake_try(**kwargs):
        calls.append(
            (
                kwargs["point_id"],
                kwargs["collection"],
                kwargs["recall_count"],
                kwargs["source_task_id"],
            )
        )
        return True

    monkeypatch.setattr(promo, "_try_promote_pending_to_proven", _fake_try)

    class _Cfg:
        enabled = True
        qdrant_url = "http://q:6333"
        experience_collection = "exp_col"

    monkeypatch.setattr("app.clients.kb_client.get_kb_config", lambda: _Cfg())

    await promo.note_kb_experience_surfaces("task-z", ["pid-1"])
    await promo.sweep_kb_experience_promotions()

    assert calls == [("pid-1", "exp_col", 1, "task-z")]
    assert promo._counts == {}
    assert promo._last_task == {}


@pytest.mark.asyncio
async def test_sweep_records_sli_metrics(monkeypatch):
    import app.kb_experience_promotion as promo
    from app.orchestrator_sli_metrics import reset_kb_experience_promotion_metrics_for_tests, snapshot_counters

    monkeypatch.setenv("KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("KB_EXPERIENCE_PROMOTE_RECALLS", "1")
    monkeypatch.delenv("KB_EXPERIENCE_PROMOTION_ROLLBACK", raising=False)
    promo.reset_promotion_state_for_tests()
    reset_kb_experience_promotion_metrics_for_tests()

    async def _ok(**_kwargs):
        return True

    monkeypatch.setattr(promo, "_try_promote_pending_to_proven", _ok)

    class _Cfg:
        enabled = True
        qdrant_url = "http://q:6333"
        experience_collection = "exp_col"

    monkeypatch.setattr("app.clients.kb_client.get_kb_config", lambda: _Cfg())

    await promo.note_kb_experience_surfaces("t-m", ["pm-1"])
    await promo.sweep_kb_experience_promotions()

    kb = snapshot_counters().get("kb_experience_promotion") or {}
    assert kb.get("promotion_sweeps") == 1
    assert kb.get("promoted_ok_total") == 1


@pytest.mark.asyncio
async def test_rollback_skips_qdrant_and_counts_rollback(monkeypatch):
    import app.kb_experience_promotion as promo
    from app.orchestrator_sli_metrics import reset_kb_experience_promotion_metrics_for_tests, snapshot_counters

    monkeypatch.setenv("KB_EXPERIENCE_PROMOTION_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("KB_EXPERIENCE_PROMOTE_RECALLS", "1")
    monkeypatch.setenv("KB_EXPERIENCE_PROMOTION_ROLLBACK", "true")
    promo.reset_promotion_state_for_tests()
    reset_kb_experience_promotion_metrics_for_tests()

    calls = []

    async def _never(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(promo, "_try_promote_pending_to_proven", _never)

    class _Cfg:
        enabled = True
        qdrant_url = "http://q:6333"
        experience_collection = "exp_col"

    monkeypatch.setattr("app.clients.kb_client.get_kb_config", lambda: _Cfg())

    await promo.note_kb_experience_surfaces("t-rb", ["rb-1"])
    await promo.sweep_kb_experience_promotions()

    assert calls == []
    kb = snapshot_counters().get("kb_experience_promotion") or {}
    assert kb.get("promotion_rollback_skips_total") == 1
