from __future__ import annotations

from datetime import datetime

from app.core import fp_persistence as persistence


def test_schema_is_created_once_before_fp_queries(monkeypatch):
    executed: list[str] = []
    monkeypatch.setattr(persistence, "_SCHEMA_READY", False)
    monkeypatch.setattr(persistence, "_execute", lambda sql, _params=(): executed.append(sql))
    monkeypatch.setattr(persistence, "_query", lambda _sql, _params=(): [])

    assert persistence.load_fp_records("task-1") == []
    assert persistence.load_fp_records("task-1") == []

    assert sum("CREATE TABLE IF NOT EXISTS tg_fp_findings" in sql for sql in executed) == 1


def test_to_dt_normalizes_utc_iso_timestamp_for_mysql():
    result = persistence._to_dt("2026-08-03T12:34:56Z")

    assert result == datetime(2026, 8, 3, 12, 34, 56)
    assert result.tzinfo is None
