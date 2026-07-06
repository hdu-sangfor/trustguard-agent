import asyncio
import importlib
import os
import sys
from pathlib import Path
from tests.paths import REPO_ROOT

import pytest
from fastapi import HTTPException


def _load_main_module():
    root = REPO_ROOT
    orch_root = str(root / "orchestrator")
    for name in list(sys.modules.keys()):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    sys.path.insert(0, orch_root)
    try:
        return importlib.import_module("app.main")
    finally:
        if orch_root in sys.path:
            sys.path.remove(orch_root)
        for name in list(sys.modules.keys()):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


def test_kb_federation_meta_crud_requires_store_flag():
    old = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
    try:
        os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        mod = _load_main_module()
        with pytest.raises(HTTPException) as err:
            asyncio.run(
                mod.upsert_v1_kb_federation_meta_create(
                    mod.KbFederationMetaUpsertBody(task_id="t1", phase="RECON")
                )
            )
        assert err.value.status_code == 404
    finally:
        if old is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old


def test_kb_federation_meta_memory_round_trip():
    old_en = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
    old_back = os.environ.get("V1_KB_FEDERATION_STORE_BACKEND")
    try:
        os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = "true"
        os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = "memory"
        mod = _load_main_module()
        Body = mod.KbFederationMetaUpsertBody
        created = asyncio.run(
            mod.upsert_v1_kb_federation_meta_create(
                Body(
                    task_id="task-crud",
                    phase="EXPLOIT",
                    capability=["http", "exploit"],
                    summary="short",
                )
            )
        )
        eid = created["id"]
        got = asyncio.run(mod.get_v1_kb_federation_meta(eid))
        assert got.get("task_id") == "task-crud"
        assert got.get("phase") == "EXPLOIT"
        assert got.get("capability") == ["http", "exploit"]

        asyncio.run(
            mod.upsert_v1_kb_federation_meta_update(
                eid,
                Body(task_id="task-crud", phase="BYPASS", kb_entry_id="kb-2"),
            )
        )
        updated = asyncio.run(mod.get_v1_kb_federation_meta(eid))
        assert updated.get("phase") == "BYPASS"
        assert updated.get("kb_entry_id") == "kb-2"

        listed = asyncio.run(mod.list_v1_kb_federation_meta_for_task("task-crud", 10))
        assert listed.get("count") == 1

        deleted = asyncio.run(mod.delete_v1_kb_federation_meta(eid))
        assert deleted.get("deleted") is True
        with pytest.raises(HTTPException) as err:
            asyncio.run(mod.get_v1_kb_federation_meta(eid))
        assert err.value.status_code == 404
    finally:
        if old_en is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old_en
        if old_back is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_BACKEND", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = old_back


def test_kb_federation_meta_get_by_id_supports_task_scope_filter():
    old_en = os.environ.get("V1_KB_FEDERATION_STORE_ENABLED")
    old_back = os.environ.get("V1_KB_FEDERATION_STORE_BACKEND")
    try:
        os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = "true"
        os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = "memory"
        mod = _load_main_module()
        Body = mod.KbFederationMetaUpsertBody
        created = asyncio.run(
            mod.upsert_v1_kb_federation_meta_create(
                Body(task_id="task-scope-1", phase="RECON", summary="scoped")
            )
        )
        eid = created["id"]

        hit = asyncio.run(mod.get_v1_kb_federation_meta(eid, task_id="task-scope-1"))
        assert hit.get("id") == eid
        assert hit.get("task_id") == "task-scope-1"

        with pytest.raises(HTTPException) as err:
            asyncio.run(mod.get_v1_kb_federation_meta(eid, task_id="task-scope-2"))
        assert err.value.status_code == 404
    finally:
        if old_en is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_ENABLED", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_ENABLED"] = old_en
        if old_back is None:
            os.environ.pop("V1_KB_FEDERATION_STORE_BACKEND", None)
        else:
            os.environ["V1_KB_FEDERATION_STORE_BACKEND"] = old_back
