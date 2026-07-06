import importlib
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT


def _orch_path():
    return str(REPO_ROOT / "orchestrator")


def _reset_store_singleton(mem_store):
    vfs = importlib.import_module("app.core.v1_kb_federation_store")
    with vfs._STORE_LOCK:
        vfs._STORE_SINGLETON = mem_store


def test_run_kb_federation_reconcile_once_none_without_sync_flag():
    sys.path.insert(0, _orch_path())
    try:
        old_s = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
        old_y = os.environ.get("V1_KB_FEDERATION_SYNC_ENABLED")
        try:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = "true"
            os.environ.pop("V1_KB_FEDERATION_SYNC_ENABLED", None)
            importlib.import_module("app.core.v1_kb_federation_sync")
            sync = importlib.import_module("app.core.v1_kb_federation_sync")
            _reset_store_singleton(
                importlib.import_module("app.core.v1_kb_federation_store").MemoryKbFederationMetaStore()
            )
            assert sync.run_kb_federation_reconcile_once() is None
        finally:
            if old_s is None:
                os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
            else:
                os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old_s
            if old_y is None:
                os.environ.pop("V1_KB_FEDERATION_SYNC_ENABLED", None)
            else:
                os.environ["V1_KB_FEDERATION_SYNC_ENABLED"] = old_y
    finally:
        sys.path.remove(_orch_path())
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_memory_reconcile_removes_stale_task_refs():
    sys.path.insert(0, _orch_path())
    try:
        vfs = importlib.import_module("app.core.v1_kb_federation_store")
        store = vfs.MemoryKbFederationMetaStore()
        _reset_store_singleton(store)
        rec = store.create({"task_id": "task-sync-1", "phase": "RECON"})
        with store._lock:
            store._by_task[rec.task_id].add("kbm-ghost-entry")
        stats = store.reconcile_indexes()
        assert stats["stale_task_refs_removed"] == 1
        with store._lock:
            assert "kbm-ghost-entry" not in store._by_task.get(rec.task_id, set())
    finally:
        sys.path.remove(_orch_path())
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_memory_reconcile_repairs_task_index():
    sys.path.insert(0, _orch_path())
    try:
        vfs = importlib.import_module("app.core.v1_kb_federation_store")
        store = vfs.MemoryKbFederationMetaStore()
        _reset_store_singleton(store)
        rec = store.create({"task_id": "task-sync-2", "phase": "EXPLOIT"})
        with store._lock:
            store._by_task[rec.task_id].discard(rec.entry_id)
        stats = store.reconcile_indexes()
        assert stats["task_index_repairs"] == 1
        with store._lock:
            assert rec.entry_id in store._by_task[rec.task_id]
    finally:
        sys.path.remove(_orch_path())
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_run_kb_federation_reconcile_once_with_flags():
    sys.path.insert(0, _orch_path())
    try:
        old_s = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
        old_y = os.environ.get("V1_KB_FEDERATION_SYNC_ENABLED")
        old_b = os.environ.get("V1_KB_FEDERATION_STORE_BACKEND")
        try:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = "true"
            os.environ["V1_KB_FEDERATION_SYNC_ENABLED"] = "true"
            os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = "memory"
            vfs = importlib.import_module("app.core.v1_kb_federation_store")
            _reset_store_singleton(vfs.MemoryKbFederationMetaStore())
            store = vfs.get_kb_federation_meta_store()
            store.create({"task_id": "task-sync-3", "phase": "BYPASS"})
            sync = importlib.import_module("app.core.v1_kb_federation_sync")
            stats = sync.run_kb_federation_reconcile_once()
            assert stats is not None
            assert "stale_task_refs_removed" in stats
        finally:
            for k, v in [
                ("V1_KB_FEDERATION_STORE_ENABLED", old_s),
                ("V1_KB_FEDERATION_SYNC_ENABLED", old_y),
                ("V1_KB_FEDERATION_STORE_BACKEND", old_b),
            ]:
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    finally:
        sys.path.remove(_orch_path())
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)
