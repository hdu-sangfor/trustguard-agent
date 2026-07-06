"""execution_store V1 artifact_refs_v1 纯函数与 Redis finish 契约单测（不启真实 Redis）。"""
from __future__ import annotations

import json

from tests.executor_test_env import prepare_executor_app_import

prepare_executor_app_import()

from app.execution_store import build_execution_finish_artifact_fields  # noqa: E402


def test_primary_falls_back_to_first_ref() -> None:
    p, j = build_execution_finish_artifact_fields(None, ["wsref:a/b/c", "wsref:a/b/d"])
    assert p == "wsref:a/b/c"
    assert json.loads(j) == ["wsref:a/b/c", "wsref:a/b/d"]


def test_explicit_primary_kept() -> None:
    p, j = build_execution_finish_artifact_fields("wsref:primary", ["wsref:other"])
    assert p == "wsref:primary"
    assert "wsref:other" in json.loads(j)


def test_dedupe_order() -> None:
    p, j = build_execution_finish_artifact_fields("", ["a", "a", "b"])
    assert p == "a"
    assert json.loads(j) == ["a", "b"]


def test_empty_list() -> None:
    p, j = build_execution_finish_artifact_fields("x", [])
    assert p == "x"
    assert json.loads(j) == []
