"""Worker Daemon 嗅探池单测。"""
from __future__ import annotations

from tests.executor_test_env import prepare_executor_app_import

prepare_executor_app_import()

from app.micro_executor.protocol import build_artifact_notice  # noqa: E402
from app.worker_daemon.sniff_pool import ArtifactSniffPool  # noqa: E402


def test_ingest_and_order() -> None:
    p = ArtifactSniffPool(request_id="req-1")
    line1 = build_artifact_notice("wsref:a/b/c", skill_id="nmap")
    line2 = build_artifact_notice("wsref:a/b/d", skill_id="nuclei")
    assert p.ingest_line(line1) is True
    assert p.ingest_line("noise") is False
    assert p.ingest_line(line2) is True
    assert p.artifact_refs_ordered() == ["wsref:a/b/c", "wsref:a/b/d"]
    recs = p.records_ordered()
    assert recs[0].skill_id == "nmap"
    assert recs[1].skill_id == "nuclei"


def test_dedupe_same_ref() -> None:
    p = ArtifactSniffPool()
    line = build_artifact_notice("wsref:same", skill_id="a")
    assert p.ingest_line(line) is True
    line2 = build_artifact_notice("wsref:same", skill_id="b")
    assert p.ingest_line(line2) is True
    assert len(p) == 1
    assert p.records_ordered()[0].skill_id == "a"
