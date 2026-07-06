import json
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


@pytest.fixture
def chunk_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def test_write_read_roundtrip(chunk_workspace: Path):
    from app.core import chunk_store

    cid = chunk_store.write_chunk(
        "task-alpha",
        chunk_type="note",
        body={"x": 1, "y": [2, 3]},
        tenant_id="tenant-1",
    )
    assert cid.startswith("chk-")
    assert len(cid) == len("chk-") + 32

    base = chunk_workspace / "task-alpha" / "chunks" / cid
    assert (base / "meta.json").is_file()
    assert (base / "content.json").is_file()
    meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    assert meta["chunk_type"] == "note"
    assert meta["tenant_id"] == "tenant-1"
    assert meta["task_id"] == "task-alpha"
    assert meta["chunk_id"] == cid
    assert "content_hash" in meta and len(meta["content_hash"]) == 64
    assert meta.get("retention") == "ephemeral"
    assert meta.get("ref_count") == 0
    assert meta.get("expires_at")

    rec = chunk_store.read_chunk("task-alpha", cid)
    assert rec is not None
    assert rec["chunk_id"] == cid
    assert rec["content"] == {"x": 1, "y": [2, 3]}


def test_read_missing_returns_none(chunk_workspace: Path):
    from app.core import chunk_store

    fake_id = "chk-" + "a" * 32
    assert chunk_store.read_chunk("task-alpha", fake_id) is None


def test_read_chunks_batch(chunk_workspace: Path):
    from app.core import chunk_store

    c1 = chunk_store.write_chunk("t1", chunk_type="a", body={"n": 1})
    c2 = chunk_store.write_chunk("t1", chunk_type="b", body={"n": 2})
    missing = "chk-" + "f" * 32
    out = chunk_store.read_chunks_batch(
        "t1",
        [c1, "not-a-chunk-id", c2, missing],
    )
    assert out[c1] is not None and out[c1]["content"]["n"] == 1
    assert out["not-a-chunk-id"] is None
    assert out[c2] is not None and out[c2]["content"]["n"] == 2
    assert out[missing] is None


def test_invalid_task_id_raises(chunk_workspace: Path):
    from app.core import chunk_store

    with pytest.raises(chunk_store.ChunkStoreError) as ei:
        chunk_store.write_chunk("../x", chunk_type="t", body={})
    assert ei.value.code == "CHUNK_INVALID_TASK_ID"


def test_invalid_chunk_type_raises(chunk_workspace: Path):
    from app.core import chunk_store

    with pytest.raises(chunk_store.ChunkStoreError) as ei:
        chunk_store.write_chunk("ok", chunk_type="  ", body={})
    assert ei.value.code == "CHUNK_INVALID_CHUNK_TYPE"


def test_body_too_large(chunk_workspace: Path, monkeypatch):
    from app.core import chunk_store

    monkeypatch.setenv("CHUNK_MAX_BODY_BYTES", "10")
    with pytest.raises(chunk_store.ChunkStoreError) as ei:
        chunk_store.write_chunk("t1", chunk_type="x", body={"data": "x" * 100})
    assert ei.value.code == "CHUNK_BODY_TOO_LARGE"
    assert ei.value.http_status == 413


def test_task_quota(chunk_workspace: Path, monkeypatch):
    from app.core import chunk_store

    monkeypatch.setenv("CHUNK_MAX_CHUNKS_PER_TASK", "2")
    chunk_store.write_chunk("tq", chunk_type="a", body={1: 1})
    chunk_store.write_chunk("tq", chunk_type="b", body={2: 2})
    with pytest.raises(chunk_store.ChunkStoreError) as ei:
        chunk_store.write_chunk("tq", chunk_type="c", body={3: 3})
    assert ei.value.code == "CHUNK_TASK_QUOTA_EXCEEDED"
    assert ei.value.http_status == 409


def test_not_json_body_raises(chunk_workspace: Path):
    from app.core import chunk_store

    with pytest.raises(chunk_store.ChunkStoreError) as ei:
        chunk_store.write_chunk("t1", chunk_type="x", body=object())  # type: ignore[arg-type]
    assert ei.value.code == "CHUNK_BODY_NOT_SERIALIZABLE"


def test_http_chunk_endpoints(chunk_workspace: Path):
    os.environ["WORKSPACE_ROOT"] = str(chunk_workspace)
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    w = client.post(
        "/v1/orchestrator/tasks/http-t/chunks",
        json={"chunk_type": "api", "body": {"k": "v"}, "tenant_id": None},
    )
    assert w.status_code == 200
    chunk_id = w.json()["chunk_id"]

    g = client.get(f"/v1/orchestrator/tasks/http-t/chunks/{chunk_id}")
    assert g.status_code == 200
    assert g.json()["content"] == {"k": "v"}

    b = client.post(
        "/v1/orchestrator/tasks/http-t/chunks:batchGet",
        json={"chunk_ids": [chunk_id, "bad", "chk-" + "0" * 32]},
    )
    assert b.status_code == 200
    chunks = b.json()["chunks"]
    assert chunks["bad"] is None
    assert chunks[chunk_id]["content"] == {"k": "v"}


def test_http_tenant_required_and_metrics(chunk_workspace: Path):
    os.environ["WORKSPACE_ROOT"] = str(chunk_workspace)
    from fastapi.testclient import TestClient

    from app.core.chunk_store_metrics import snapshot
    from app.main import app

    client = TestClient(app)
    snap0 = snapshot()
    w = client.post(
        "/v1/orchestrator/tasks/http-z/chunks",
        json={"chunk_type": "sec", "body": {"a": 1}, "tenant_id": "tenant-A"},
    )
    assert w.status_code == 200
    cid = w.json()["chunk_id"]
    assert snapshot()["writes_ok"] == snap0["writes_ok"] + 1

    m = client.get("/v1/orchestrator/chunk-store/metrics")
    assert m.status_code == 200
    mj = m.json()
    assert "writes_ok" in mj["chunk_store_metrics"]
    assert "embed_quota_acquire_ok" in mj["kb_embed_quota_metrics"]

    no_hdr = client.get(f"/v1/orchestrator/tasks/http-z/chunks/{cid}")
    assert no_hdr.status_code == 403
    err = no_hdr.json()["detail"]["structured_error"]
    assert err["code"] == "CHUNK_TENANT_REQUIRED"

    wrong = client.get(
        f"/v1/orchestrator/tasks/http-z/chunks/{cid}",
        headers={"X-Tenant-Id": "other"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["detail"]["structured_error"]["code"] == "CHUNK_TENANT_MISMATCH"

    ok = client.get(
        f"/v1/orchestrator/tasks/http-z/chunks/{cid}",
        headers={"X-Tenant-Id": "tenant-A"},
    )
    assert ok.status_code == 200
    assert ok.json()["content"] == {"a": 1}

    batch = client.post(
        "/v1/orchestrator/tasks/http-z/chunks:batchGet",
        json={"chunk_ids": [cid]},
        headers={"X-Tenant-Id": "tenant-A"},
    )
    assert batch.status_code == 200
    assert batch.json()["chunks"][cid]["content"] == {"a": 1}

    batch_denied = client.post(
        "/v1/orchestrator/tasks/http-z/chunks:batchGet",
        json={"chunk_ids": [cid]},
    )
    assert batch_denied.status_code == 200
    assert batch_denied.json()["chunks"][cid] is None


def test_http_not_found_structured(chunk_workspace: Path):
    os.environ["WORKSPACE_ROOT"] = str(chunk_workspace)
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    missing = "chk-" + "9" * 32
    r = client.get(f"/v1/orchestrator/tasks/http-t/chunks/{missing}")
    assert r.status_code == 404
    assert r.json()["detail"]["structured_error"]["code"] == "CHUNK_NOT_FOUND"


def test_http_batch_too_many(chunk_workspace: Path):
    os.environ["WORKSPACE_ROOT"] = str(chunk_workspace)
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post(
        "/v1/orchestrator/tasks/http-t/chunks:batchGet",
        json={"chunk_ids": ["chk-" + "a" * 32] * 501},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["structured_error"]["code"] == "CHUNK_BATCH_TOO_MANY"


def test_gc_sweep_deletes_expired_ephemeral(chunk_workspace: Path, monkeypatch):
    monkeypatch.setenv("CHUNK_GC_ENABLED", "true")
    from app.core import chunk_store

    cid = chunk_store.write_chunk("g1", chunk_type="t", body={"a": 1}, ttl_seconds=3600)
    meta_path = chunk_workspace / "g1" / "chunks" / cid / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = "2000-01-01T00:00:00+00:00"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    r = chunk_store.gc_sweep_task("g1")
    assert r["deleted_count"] == 1
    assert chunk_store.read_chunk("g1", cid) is None


def test_gc_ref_count_blocks_delete(chunk_workspace: Path, monkeypatch):
    monkeypatch.setenv("CHUNK_GC_ENABLED", "true")
    from app.core import chunk_store

    cid = chunk_store.write_chunk("g2", chunk_type="t", body={})
    meta_path = chunk_workspace / "g2" / "chunks" / cid / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = "2000-01-01T00:00:00+00:00"
    meta["ref_count"] = 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    r = chunk_store.gc_sweep_task("g2")
    assert r["deleted_count"] == 0
    assert chunk_store.read_chunk("g2", cid) is not None


def test_gc_skips_pinned(chunk_workspace: Path, monkeypatch):
    monkeypatch.setenv("CHUNK_GC_ENABLED", "true")
    from app.core import chunk_store

    cid = chunk_store.write_chunk("g3", chunk_type="t", body={}, retention="pinned")
    meta_path = chunk_workspace / "g3" / "chunks" / cid / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = "2000-01-01T00:00:00+00:00"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    r = chunk_store.gc_sweep_task("g3")
    assert r["deleted_count"] == 0


def test_lazy_ttl_delete_on_read(chunk_workspace: Path, monkeypatch):
    monkeypatch.setenv("CHUNK_GC_ENABLED", "true")
    from app.core import chunk_store
    from app.core.chunk_store_metrics import snapshot

    cid = chunk_store.write_chunk("g4", chunk_type="t", body={"x": 1})
    meta_path = chunk_workspace / "g4" / "chunks" / cid / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = "2000-01-01T00:00:00+00:00"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    before = snapshot()["lazy_ttl_deletes"]
    assert chunk_store.read_chunk("g4", cid) is None
    assert snapshot()["lazy_ttl_deletes"] == before + 1


def test_adjust_ref_gc_and_http_sweep(chunk_workspace: Path, monkeypatch):
    monkeypatch.setenv("CHUNK_GC_ENABLED", "true")
    os.environ["WORKSPACE_ROOT"] = str(chunk_workspace)
    from fastapi.testclient import TestClient

    from app.core import chunk_store
    from app.main import app

    cid = chunk_store.write_chunk("ht", chunk_type="t", body={})
    meta_path = chunk_workspace / "ht" / "chunks" / cid / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = "2000-01-01T00:00:00+00:00"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    chunk_store.adjust_chunk_ref("ht", cid, 1)
    assert chunk_store.gc_sweep_task("ht")["deleted_count"] == 0

    chunk_store.adjust_chunk_ref("ht", cid, -1)
    assert chunk_store.gc_sweep_task("ht")["deleted_count"] == 1

    cid2 = chunk_store.write_chunk("ht", chunk_type="t2", body={})
    meta_path2 = chunk_workspace / "ht" / "chunks" / cid2 / "meta.json"
    meta2 = json.loads(meta_path2.read_text(encoding="utf-8"))
    meta2["expires_at"] = "2000-01-01T00:00:00+00:00"
    meta_path2.write_text(json.dumps(meta2), encoding="utf-8")

    client = TestClient(app)
    sw = client.post("/v1/orchestrator/chunk-store/gc:sweep", json={"task_id": "ht"})
    assert sw.status_code == 200
    assert sw.json()["deleted_count"] == 1


def test_set_retention_ephemeral_rewrites_expiry(chunk_workspace: Path, monkeypatch):
    monkeypatch.setenv("CHUNK_GC_ENABLED", "true")
    from app.core import chunk_store

    cid = chunk_store.write_chunk("sr", chunk_type="t", body={}, retention="pinned")
    chunk_store.set_chunk_retention("sr", cid, "ephemeral")
    meta = json.loads(
        (chunk_workspace / "sr" / "chunks" / cid / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["retention"] == "ephemeral"
    assert meta.get("expires_at")
