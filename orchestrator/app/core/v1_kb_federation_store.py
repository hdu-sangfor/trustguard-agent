"""
KB 联邦旁路元数据存储（PoC）。

- 管理面 CRUD + 进程内 memory 或可选 Redis；不接入主 state_machine。
- 仅持久化 `v1-kb-metadata-contract.md` 允许的非密钥字段子集；拒绝常见密钥形值。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

_ALLOWED_ENTRY_KEYS = frozenset(
    {
        "task_id",
        "plan_item_id",
        "agent_id",
        "phase",
        "capability",
        "kb_entry_id",
        "chunk_ref",
        "artifact_ref",
        "parent_ref",
        "content_type",
        "summary",
        "created_at",
        "updated_at",
    }
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)token"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def kb_federation_store_enabled_from_env() -> bool:
    raw = (os.getenv("V1_KB_FEDERATION_STORE_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def kb_federation_store_backend_from_env() -> str:
    raw = (os.getenv("V1_KB_FEDERATION_STORE_BACKEND") or "").strip().lower()
    if raw in ("redis", "memory"):
        return raw
    if (os.getenv("V1_KB_FEDERATION_STORE_REDIS_URL") or "").strip():
        return "redis"
    return "memory"


def kb_federation_store_redis_url() -> str:
    return (os.getenv("V1_KB_FEDERATION_STORE_REDIS_URL") or "").strip()


def kb_federation_store_redis_prefix() -> str:
    return (os.getenv("V1_KB_FEDERATION_STORE_REDIS_KEY_PREFIX") or "v1:kb_fed").strip() or "v1:kb_fed"


class KbFederationStoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _looks_secret_scalar(val: str) -> bool:
    s = val.strip()
    low = s.lower()
    for pat in _SECRET_VALUE_PATTERNS:
        if pat.search(low):
            return True
    if s.startswith("eyJ") and s.count(".") == 2:
        return True
    if "bearer " in low:
        return True
    return False


def normalize_federation_meta(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        raise KbFederationStoreError("EMPTY_BODY", "body must be a JSON object")
    if not isinstance(raw, dict):
        raise KbFederationStoreError("INVALID_JSON_OBJECT", "body must be a JSON object")

    out: dict[str, Any] = {}
    for key, val in raw.items():
        k = (key or "").strip()
        if not k or k not in _ALLOWED_ENTRY_KEYS:
            continue
        if val is None:
            continue
        if k == "capability":
            if isinstance(val, str):
                caps = [val.strip()] if val.strip() else []
            elif isinstance(val, list):
                caps = []
                for item in val:
                    if isinstance(item, str) and item.strip():
                        caps.append(item.strip())
                    else:
                        raise KbFederationStoreError("INVALID_CAPABILITY", "capability must be string or string[]")
            else:
                raise KbFederationStoreError("INVALID_CAPABILITY", "capability must be string or string[]")
            out[k] = caps
            continue

        if not isinstance(val, str):
            raise KbFederationStoreError("INVALID_FIELD_TYPE", f"field {k} must be a string")
        v = val.strip()
        if not v:
            continue
        if len(v) > 2048:
            raise KbFederationStoreError("FIELD_TOO_LONG", f"field {k} exceeds max length")
        if k == "summary" and len(v) > 512:
            raise KbFederationStoreError("SUMMARY_TOO_LONG", "summary exceeds max length")
        if _looks_secret_scalar(v):
            raise KbFederationStoreError("SECRET_LIKE_VALUE", f"field {k} looks like secret material")
        out[k] = v

    task_id = (out.get("task_id") or "").strip() if isinstance(out.get("task_id"), str) else ""
    if not task_id:
        raise KbFederationStoreError("TASK_ID_REQUIRED", "task_id is required")
    out["task_id"] = task_id
    return out


@dataclass(frozen=True)
class KbFederationMetaRecord:
    entry_id: str
    task_id: str
    meta: dict[str, Any]

    def to_public_dict(self) -> dict[str, Any]:
        body = {"id": self.entry_id, **self.meta}
        return body


class KbFederationMetaStore(ABC):
    @abstractmethod
    def create(self, meta: dict[str, Any]) -> KbFederationMetaRecord:
        raise NotImplementedError

    @abstractmethod
    def get(self, entry_id: str) -> KbFederationMetaRecord | None:
        raise NotImplementedError

    @abstractmethod
    def get_for_task(self, task_id: str, entry_id: str) -> KbFederationMetaRecord | None:
        raise NotImplementedError

    @abstractmethod
    def update(self, entry_id: str, meta: dict[str, Any]) -> KbFederationMetaRecord:
        raise NotImplementedError

    @abstractmethod
    def delete(self, entry_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_for_task(self, task_id: str, *, limit: int = 50) -> list[KbFederationMetaRecord]:
        raise NotImplementedError

    @abstractmethod
    def count_all(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def aggregate_phases(self) -> dict[str, int]:
        raise NotImplementedError

    @abstractmethod
    def distinct_task_count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def sample_entry_ids(self, *, limit: int = 8) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def reconcile_indexes(self) -> dict[str, int]:
        """校正 task 索引与 all 集合一致性；仅 PoC 运维用途，不触碰 state_machine。"""
        raise NotImplementedError


class MemoryKbFederationMetaStore(KbFederationMetaStore):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[str, KbFederationMetaRecord] = {}
        self._by_task: dict[str, set[str]] = {}

    def create(self, meta: dict[str, Any]) -> KbFederationMetaRecord:
        entry_id = f"kbm-{uuid.uuid4().hex[:16]}"
        rec = KbFederationMetaRecord(entry_id=entry_id, task_id=meta["task_id"], meta=dict(meta))
        with self._lock:
            self._by_id[entry_id] = rec
            self._by_task.setdefault(rec.task_id, set()).add(entry_id)
        return rec

    def get(self, entry_id: str) -> KbFederationMetaRecord | None:
        with self._lock:
            return self._by_id.get(entry_id)

    def get_for_task(self, task_id: str, entry_id: str) -> KbFederationMetaRecord | None:
        tid = (task_id or "").strip()
        if not tid:
            return None
        with self._lock:
            rec = self._by_id.get(entry_id)
            if rec is None or rec.task_id != tid:
                return None
            return rec

    def update(self, entry_id: str, meta: dict[str, Any]) -> KbFederationMetaRecord:
        with self._lock:
            old = self._by_id.get(entry_id)
            if old is None:
                raise KbFederationStoreError("NOT_FOUND", "entry not found")
            new_task = meta["task_id"]
            if new_task != old.task_id:
                old_set = self._by_task.get(old.task_id)
                if old_set is not None:
                    old_set.discard(entry_id)
                    if not old_set:
                        self._by_task.pop(old.task_id, None)
                self._by_task.setdefault(new_task, set()).add(entry_id)
            rec = KbFederationMetaRecord(entry_id=entry_id, task_id=new_task, meta=dict(meta))
            self._by_id[entry_id] = rec
            return rec

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            old = self._by_id.pop(entry_id, None)
            if old is None:
                return False
            tset = self._by_task.get(old.task_id)
            if tset is not None:
                tset.discard(entry_id)
                if not tset:
                    self._by_task.pop(old.task_id, None)
            return True

    def list_for_task(self, task_id: str, *, limit: int = 50) -> list[KbFederationMetaRecord]:
        tid = (task_id or "").strip()
        if not tid:
            return []
        lim = max(1, min(limit, 200))
        with self._lock:
            ids = sorted(self._by_task.get(tid, set()))
            out: list[KbFederationMetaRecord] = []
            for eid in ids[:lim]:
                rec = self._by_id.get(eid)
                if rec is not None:
                    out.append(rec)
            return out

    def count_all(self) -> int:
        with self._lock:
            return len(self._by_id)

    def aggregate_phases(self) -> dict[str, int]:
        phases = {"RECON": 0, "EXPLOIT": 0, "BYPASS": 0, "OTHER": 0}
        with self._lock:
            for rec in self._by_id.values():
                ph_raw = rec.meta.get("phase")
                if not isinstance(ph_raw, str):
                    phases["OTHER"] += 1
                    continue
                key = ph_raw.strip().upper()
                if key in phases:
                    phases[key] += 1
                else:
                    phases["OTHER"] += 1
        return phases

    def distinct_task_count(self) -> int:
        with self._lock:
            return sum(1 for ids in self._by_task.values() if ids)

    def sample_entry_ids(self, *, limit: int = 8) -> list[str]:
        lim = max(1, min(limit, 50))
        with self._lock:
            return sorted(self._by_id.keys())[:lim]

    def reconcile_indexes(self) -> dict[str, int]:
        stale_task_refs_removed = 0
        task_index_repairs = 0
        with self._lock:
            for tid in list(self._by_task.keys()):
                eids = self._by_task.get(tid)
                if not eids:
                    self._by_task.pop(tid, None)
                    continue
                for eid in list(eids):
                    if eid not in self._by_id:
                        eids.discard(eid)
                        stale_task_refs_removed += 1
                if not eids:
                    self._by_task.pop(tid, None)
            for eid, rec in self._by_id.items():
                tset = self._by_task.setdefault(rec.task_id, set())
                if eid not in tset:
                    tset.add(eid)
                    task_index_repairs += 1
        return {
            "stale_task_refs_removed": stale_task_refs_removed,
            "task_index_repairs": task_index_repairs,
            "orphan_all_ids_removed": 0,
        }


class RedisKbFederationMetaStore(KbFederationMetaStore):
    def __init__(self, url: str, prefix: str) -> None:
        import redis

        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._prefix = prefix.rstrip(":")

    def _entry_key(self, entry_id: str) -> str:
        return f"{self._prefix}:entry:{entry_id}"

    def _task_set_key(self, task_id: str) -> str:
        return f"{self._prefix}:task:{task_id}:entries"

    def _all_set_key(self) -> str:
        return f"{self._prefix}:all_entries"

    def create(self, meta: dict[str, Any]) -> KbFederationMetaRecord:
        entry_id = f"kbm-{uuid.uuid4().hex[:16]}"
        rec = KbFederationMetaRecord(entry_id=entry_id, task_id=meta["task_id"], meta=dict(meta))
        payload = json.dumps(rec.meta, ensure_ascii=False, separators=(",", ":"))
        ex = self._redis
        ex.hset(self._entry_key(entry_id), mapping={"task_id": rec.task_id, "meta": payload})
        ex.sadd(self._task_set_key(rec.task_id), entry_id)
        ex.sadd(self._all_set_key(), entry_id)
        return rec

    def get(self, entry_id: str) -> KbFederationMetaRecord | None:
        ex = self._redis
        data = ex.hgetall(self._entry_key(entry_id))
        if not data:
            return None
        task_id = data.get("task_id") or ""
        raw_meta = data.get("meta") or "{}"
        try:
            meta_obj = json.loads(raw_meta)
        except Exception:
            meta_obj = {}
        if not isinstance(meta_obj, dict):
            meta_obj = {}
        return KbFederationMetaRecord(entry_id=entry_id, task_id=task_id, meta=meta_obj)

    def get_for_task(self, task_id: str, entry_id: str) -> KbFederationMetaRecord | None:
        tid = (task_id or "").strip()
        if not tid:
            return None
        rec = self.get(entry_id)
        if rec is None or rec.task_id != tid:
            return None
        return rec

    def update(self, entry_id: str, meta: dict[str, Any]) -> KbFederationMetaRecord:
        old = self.get(entry_id)
        if old is None:
            raise KbFederationStoreError("NOT_FOUND", "entry not found")
        ex = self._redis
        if meta["task_id"] != old.task_id:
            old_task_key = self._task_set_key(old.task_id)
            ex.srem(old_task_key, entry_id)
            if ex.scard(old_task_key) == 0:
                ex.delete(old_task_key)
            ex.sadd(self._task_set_key(meta["task_id"]), entry_id)
        payload = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        ex.hset(self._entry_key(entry_id), mapping={"task_id": meta["task_id"], "meta": payload})
        return KbFederationMetaRecord(entry_id=entry_id, task_id=meta["task_id"], meta=dict(meta))

    def delete(self, entry_id: str) -> bool:
        old = self.get(entry_id)
        if old is None:
            return False
        ex = self._redis
        ex.delete(self._entry_key(entry_id))
        task_key = self._task_set_key(old.task_id)
        ex.srem(task_key, entry_id)
        if ex.scard(task_key) == 0:
            ex.delete(task_key)
        ex.srem(self._all_set_key(), entry_id)
        return True

    def list_for_task(self, task_id: str, *, limit: int = 50) -> list[KbFederationMetaRecord]:
        tid = (task_id or "").strip()
        if not tid:
            return []
        lim = max(1, min(limit, 200))
        ex = self._redis
        ids = sorted(ex.smembers(self._task_set_key(tid)))
        out: list[KbFederationMetaRecord] = []
        for eid in ids[:lim]:
            rec = self.get(eid)
            if rec is not None:
                out.append(rec)
        return out

    def count_all(self) -> int:
        return int(self._redis.scard(self._all_set_key()))

    def aggregate_phases(self) -> dict[str, int]:
        phases = {"RECON": 0, "EXPLOIT": 0, "BYPASS": 0, "OTHER": 0}
        ex = self._redis
        ids = ex.smembers(self._all_set_key())
        for eid in ids:
            rec = self.get(eid)
            if rec is None:
                continue
            ph_raw = rec.meta.get("phase")
            if not isinstance(ph_raw, str):
                phases["OTHER"] += 1
                continue
            key = ph_raw.strip().upper()
            if key in phases:
                phases[key] += 1
            else:
                phases["OTHER"] += 1
        return phases

    def distinct_task_count(self) -> int:
        ex = self._redis
        prefix = self._prefix
        cursor = 0
        tasks = 0
        pattern = f"{prefix}:task:*:entries"
        while True:
            cursor, keys = ex.scan(cursor=cursor, match=pattern, count=200)
            for k in keys:
                try:
                    if ex.scard(k) > 0:
                        tasks += 1
                except Exception:
                    continue
            if cursor == 0:
                break
        return tasks

    def sample_entry_ids(self, *, limit: int = 8) -> list[str]:
        lim = max(1, min(limit, 50))
        ex = self._redis
        ids = sorted(ex.smembers(self._all_set_key()))
        return ids[:lim]

    def reconcile_indexes(self) -> dict[str, int]:
        ex = self._redis
        orphan_all_ids_removed = 0
        stale_task_refs_removed = 0
        task_index_repairs = 0
        all_key = self._all_set_key()
        for eid in list(ex.smembers(all_key)):
            if not ex.exists(self._entry_key(eid)):
                ex.srem(all_key, eid)
                orphan_all_ids_removed += 1
        pfx = f"{self._prefix}:task:"
        cursor = 0
        pattern = f"{self._prefix}:task:*:entries"
        while True:
            cursor, keys = ex.scan(cursor=cursor, match=pattern, count=200)
            for key in keys:
                if not key.startswith(pfx) or not key.endswith(":entries"):
                    continue
                tid = key[len(pfx) : -len(":entries")]
                for eid in list(ex.smembers(key)):
                    rec = self.get(eid)
                    if rec is None or rec.task_id != tid:
                        ex.srem(key, eid)
                        stale_task_refs_removed += 1
                if ex.scard(key) == 0:
                    ex.delete(key)
            if cursor == 0:
                break
        for eid in list(ex.smembers(all_key)):
            rec = self.get(eid)
            if rec is None:
                continue
            tkey = self._task_set_key(rec.task_id)
            if not ex.sismember(tkey, eid):
                ex.sadd(tkey, eid)
                task_index_repairs += 1
        return {
            "stale_task_refs_removed": stale_task_refs_removed,
            "task_index_repairs": task_index_repairs,
            "orphan_all_ids_removed": orphan_all_ids_removed,
        }


_STORE_SINGLETON: KbFederationMetaStore | None = None
_STORE_LOCK = threading.Lock()


def get_kb_federation_meta_store() -> KbFederationMetaStore:
    global _STORE_SINGLETON
    with _STORE_LOCK:
        if _STORE_SINGLETON is not None:
            return _STORE_SINGLETON
        backend = kb_federation_store_backend_from_env()
        if backend == "redis":
            url = kb_federation_store_redis_url()
            if not url:
                _STORE_SINGLETON = MemoryKbFederationMetaStore()
            else:
                _STORE_SINGLETON = RedisKbFederationMetaStore(url, kb_federation_store_redis_prefix())
        else:
            _STORE_SINGLETON = MemoryKbFederationMetaStore()
        return _STORE_SINGLETON
