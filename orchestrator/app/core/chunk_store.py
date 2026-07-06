"""
按 task 分区的 Chunk 持久化：写入、按 id 读、批量读、引用计数、TTL、GC（r3c）。

存储路径：{WORKSPACE_ROOT}/{task_id}/chunks/{chunk_id}/meta.json + content.json

限额（r3b）：CHUNK_MAX_BODY_BYTES、CHUNK_MAX_CHUNKS_PER_TASK、CHUNK_MAX_CHUNK_TYPE_LEN、CHUNK_MAX_TENANT_ID_LEN

生命周期（r3c）：
- meta.retention: ephemeral | pinned | proven（ephemeral 受 TTL + ref_count 约束）
- meta.ref_count: 被 Plan 等持有引用时 >0，GC 不删
- meta.expires_at / ttl_seconds：ephemeral 过期且 ref_count==0 时可物理删除
- CHUNK_DEFAULT_TTL_SECONDS（默认 7 天）；0 表示不按时间过期
- CHUNK_GC_ENABLED：关则不做懒删与 sweep 删除
- CHUNK_GC_INTERVAL_SECONDS>0：编排器 lifespan 中间隔异步 sweep 全 workspace
- ORCH_CHUNK_QUOTA_WARN_RATIO：接近 CHUNK_MAX_CHUNKS_PER_TASK 时软告警（日志 + chunk_quota_warn_events 指标）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from app.core import chunk_store_metrics as _m

logger = logging.getLogger(__name__)

_chunk_quota_warned_tasks: set[str] = set()
_CHUNK_WARN_TRACK_MAX = 2048

CHUNK_ID_PREFIX = "chk-"
CHUNK_SCHEMA_VERSION = 1
CHUNK_BATCH_GET_MAX = 500

Retention = Literal["ephemeral", "pinned", "proven"]


class ChunkStoreError(Exception):
    """稳定错误码，供 HTTP 映射为 structured_error。"""

    __slots__ = ("code", "message", "http_status", "details")

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        super().__init__(message)


def chunk_store_error_http_detail(err: ChunkStoreError) -> dict[str, Any]:
    return {
        "structured_error": {
            "kind": "chunk_store",
            "code": err.code,
            "message": err.message,
            "details": err.details,
        }
    }


def _workspace_root() -> Path:
    return Path(os.getenv("WORKSPACE_ROOT", "/data/workspace")).resolve()


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def gc_enabled() -> bool:
    return (os.getenv("CHUNK_GC_ENABLED", "true") or "true").strip().lower() in ("1", "true", "yes", "on")


def default_ttl_seconds() -> int:
    """0 表示不按时间淘汰（仅 ref 与 retention 仍生效）。"""
    return max(0, _int_env("CHUNK_DEFAULT_TTL_SECONDS", 7 * 24 * 3600))


def max_ref_adjust_batch() -> int:
    return max(1, min(5000, _int_env("CHUNK_MAX_REF_ADJUST_BATCH", 500)))


def max_ref_delta_abs() -> int:
    return max(1, min(1_000_000, _int_env("CHUNK_REF_DELTA_ABS_MAX", 10_000)))


def max_body_bytes() -> int:
    """下限 1 字节，便于单测覆盖；生产请在编排侧设合理默认（如 4MiB）。"""
    return max(1, _int_env("CHUNK_MAX_BODY_BYTES", 4 * 1024 * 1024))


def max_chunks_per_task() -> int:
    return max(1, _int_env("CHUNK_MAX_CHUNKS_PER_TASK", 10_000))


def chunk_quota_soft_warn_ratio() -> float:
    """0=关闭软告警；默认 0.85（达到上限 85% 时每个 task 打一次日志 + 指标）。"""
    raw = (os.getenv("ORCH_CHUNK_QUOTA_WARN_RATIO") or "0.85").strip()
    try:
        v = float(raw)
    except ValueError:
        return 0.85
    return max(0.0, min(v, 1.0))


def _maybe_record_chunk_quota_soft_warn(task_id: str, current_count: int, quota: int) -> None:
    ratio = chunk_quota_soft_warn_ratio()
    if ratio <= 0 or quota <= 0:
        return
    threshold = max(1, int(quota * ratio))
    if current_count < threshold or current_count >= quota:
        return
    global _chunk_quota_warned_tasks
    if task_id in _chunk_quota_warned_tasks:
        return
    if len(_chunk_quota_warned_tasks) >= _CHUNK_WARN_TRACK_MAX:
        _chunk_quota_warned_tasks.clear()
    _chunk_quota_warned_tasks.add(task_id)
    _m.record_chunk_quota_warn()
    logger.warning(
        "chunk quota soft warn: task_id=%s chunk_count=%s threshold=%s hard_limit=%s",
        task_id,
        current_count,
        threshold,
        quota,
    )


def max_chunk_type_len() -> int:
    return max(1, min(512, _int_env("CHUNK_MAX_CHUNK_TYPE_LEN", 128)))


def max_tenant_id_len() -> int:
    return max(1, min(512, _int_env("CHUNK_MAX_TENANT_ID_LEN", 128)))


def generate_chunk_id() -> str:
    """生成唯一 chunk_id（chk- + uuid4 hex）。"""
    return f"{CHUNK_ID_PREFIX}{uuid.uuid4().hex}"


def _validate_task_id(task_id: str) -> str:
    tid = (task_id or "").strip()
    if not tid or ".." in tid or "/" in tid or "\\" in tid:
        raise ChunkStoreError("CHUNK_INVALID_TASK_ID", "invalid task_id", http_status=400)
    return tid


def _validate_chunk_id(chunk_id: str) -> str:
    cid = (chunk_id or "").strip()
    if not cid.startswith(CHUNK_ID_PREFIX):
        raise ChunkStoreError("CHUNK_INVALID_CHUNK_ID", "chunk_id must start with chk-", http_status=400)
    rest = cid[len(CHUNK_ID_PREFIX) :]
    if len(rest) < 16 or len(rest) > 128:
        raise ChunkStoreError("CHUNK_INVALID_CHUNK_ID", "chunk_id length invalid", http_status=400)
    for ch in rest:
        if ch.isalnum() or ch in "-_":
            continue
        raise ChunkStoreError("CHUNK_INVALID_CHUNK_ID", "chunk_id contains invalid characters", http_status=400)
    if ".." in cid or "/" in cid or "\\" in cid:
        raise ChunkStoreError("CHUNK_INVALID_CHUNK_ID", "chunk_id traversal", http_status=400)
    return cid


def _validate_chunk_type(chunk_type: str) -> str:
    ct = (chunk_type or "").strip()
    if not ct:
        raise ChunkStoreError("CHUNK_INVALID_CHUNK_TYPE", "chunk_type required", http_status=400)
    mxl = max_chunk_type_len()
    if len(ct) > mxl:
        raise ChunkStoreError(
            "CHUNK_CHUNK_TYPE_TOO_LONG",
            f"chunk_type exceeds max length ({mxl})",
            http_status=400,
            details={"max_len": mxl},
        )
    return ct


def _validate_tenant_id_optional(tenant_id: str | None) -> str | None:
    if tenant_id is None:
        return None
    t = tenant_id.strip()
    if not t:
        return None
    mxl = max_tenant_id_len()
    if len(t) > mxl:
        raise ChunkStoreError(
            "CHUNK_TENANT_ID_TOO_LONG",
            f"tenant_id exceeds max length ({mxl})",
            http_status=400,
            details={"max_len": mxl},
        )
    return t


def _validate_retention(retention: str) -> Retention:
    r = (retention or "ephemeral").strip().lower()
    if r not in ("ephemeral", "pinned", "proven"):
        raise ChunkStoreError(
            "CHUNK_INVALID_RETENTION",
            "retention must be ephemeral, pinned, or proven",
            http_status=400,
        )
    return r  # type: ignore[return-value]


def _parse_dt_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _meta_retention(meta: dict[str, Any]) -> str:
    r = (meta.get("retention") or "ephemeral")
    if isinstance(r, str):
        return r.strip().lower()
    return "ephemeral"


def _meta_ref_count(meta: dict[str, Any]) -> int:
    try:
        return max(0, int(meta.get("ref_count") or 0))
    except (TypeError, ValueError):
        return 0


def effective_expires_at(meta: dict[str, Any]) -> datetime | None:
    """用于判定是否已过 TTL（含旧 meta 无 expires_at 时按 created_at+ttl 推算）。"""
    if _meta_retention(meta) != "ephemeral":
        return None
    raw_exp = meta.get("expires_at")
    if raw_exp and isinstance(raw_exp, str):
        try:
            return _parse_dt_iso(raw_exp)
        except ValueError:
            pass
    ttl_s = meta.get("ttl_seconds")
    if ttl_s is None:
        eff = default_ttl_seconds()
    else:
        try:
            eff = int(ttl_s)
        except (TypeError, ValueError):
            eff = default_ttl_seconds()
    if eff <= 0:
        return None
    created_raw = meta.get("created_at")
    if not created_raw or not isinstance(created_raw, str):
        return None
    try:
        created = _parse_dt_iso(created_raw)
    except ValueError:
        return None
    return created + timedelta(seconds=eff)


def _is_ttl_expired(meta: dict[str, Any], now: datetime) -> bool:
    exp = effective_expires_at(meta)
    if exp is None:
        return False
    return now > exp


def _is_gc_eligible(meta: dict[str, Any], now: datetime) -> bool:
    if not gc_enabled():
        return False
    if _meta_retention(meta) != "ephemeral":
        return False
    if _meta_ref_count(meta) > 0:
        return False
    return _is_ttl_expired(meta, now)


def _chunk_dir(task_id: str, chunk_id: str) -> Path:
    root = _workspace_root()
    tid = _validate_task_id(task_id)
    cid = _validate_chunk_id(chunk_id)
    base = (root / tid / "chunks" / cid).resolve()
    try:
        base.relative_to(root.resolve())
    except ValueError as exc:
        raise ChunkStoreError("CHUNK_PATH_ESCAPE", "chunk path escapes workspace", http_status=400) from exc
    return base


def _task_chunks_dir(task_id: str) -> Path:
    root = _workspace_root()
    tid = _validate_task_id(task_id)
    p = (root / tid / "chunks").resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError as exc:
        raise ChunkStoreError("CHUNK_PATH_ESCAPE", "task chunks path escapes workspace", http_status=400) from exc
    return p


def count_chunks_for_task(task_id: str) -> int:
    p = _task_chunks_dir(task_id)
    if not p.is_dir():
        return 0
    return sum(1 for x in p.iterdir() if x.is_dir())


def list_chunk_ids_for_task(task_id: str) -> list[str]:
    """
    返回该任务 workspace 下所有已落盘 chunk 的 chunk_id 列表（目录名即 chunk_id）。
    用于预校验：判断 LLM plan 引用的 chunk_id 是否真实存在（避免幻觉 ID 进入派发循环）。
    不验证 TTL/tenant；轻量目录枚举，仅在 plan 校验时调用。
    """
    try:
        p = _task_chunks_dir(task_id)
    except ChunkStoreError:
        return []
    if not p.is_dir():
        return []
    out: list[str] = []
    for x in p.iterdir():
        if x.is_dir():
            name = x.name
            if name.startswith("chk-") and len(name) > len("chk-"):
                out.append(name)
    return out


def _tenant_issue(
    meta: dict[str, Any],
    expect_tenant_id: str | None,
    *,
    require_tenant_when_bound: bool,
) -> str | None:
    stored = meta.get("tenant_id")
    if not stored:
        return None
    if expect_tenant_id is None:
        if require_tenant_when_bound:
            return "CHUNK_TENANT_REQUIRED"
        return None
    if stored != expect_tenant_id:
        return "CHUNK_TENANT_MISMATCH"
    return None


def _rmtree_chunk(cdir: Path) -> None:
    shutil.rmtree(cdir, ignore_errors=False)


def _persist_meta(cdir: Path, meta: dict[str, Any]) -> None:
    (cdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def write_chunk(
    task_id: str,
    *,
    chunk_type: str,
    body: Any,
    tenant_id: str | None = None,
    chunk_id: str | None = None,
    retention: str = "ephemeral",
    ttl_seconds: int | None = None,
) -> str:
    """
    写入 chunk。body 须可 JSON 序列化。
    retention=pinned|proven 时不写 expires_at（不受 TTL GC）；ephemeral 可传 ttl_seconds 覆盖默认 TTL。
    """
    try:
        tid = _validate_task_id(task_id)
        ret = _validate_retention(retention)
        ct = _validate_chunk_type(chunk_type)
        ten = _validate_tenant_id_optional(tenant_id)
        quota = max_chunks_per_task()
        _cur_count = count_chunks_for_task(tid)
        if _cur_count >= quota:
            raise ChunkStoreError(
                "CHUNK_TASK_QUOTA_EXCEEDED",
                f"chunk count for task exceeds limit ({quota})",
                http_status=409,
                details={"limit": quota},
            )
        _maybe_record_chunk_quota_soft_warn(tid, _cur_count, quota)

        try:
            content_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ChunkStoreError(
                "CHUNK_BODY_NOT_SERIALIZABLE",
                "body is not JSON-serializable",
                http_status=400,
            ) from exc

        mxb = max_body_bytes()
        if len(content_bytes) > mxb:
            raise ChunkStoreError(
                "CHUNK_BODY_TOO_LARGE",
                f"serialized body exceeds max bytes ({mxb})",
                http_status=413,
                details={"max_bytes": mxb, "size_bytes": len(content_bytes)},
            )

        cid = _validate_chunk_id(chunk_id) if chunk_id else generate_chunk_id()
        cdir = _chunk_dir(tid, cid)
        try:
            cdir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ChunkStoreError(
                "CHUNK_ID_COLLISION",
                "chunk_id already exists",
                http_status=409,
                details={"chunk_id": cid},
            ) from exc

        now = datetime.now(timezone.utc)
        expires_at_str: str | None = None
        stored_ttl: int | None = None
        if ret == "ephemeral":
            if ttl_seconds is not None:
                if ttl_seconds < 0:
                    raise ChunkStoreError("CHUNK_INVALID_TTL", "ttl_seconds must be >= 0", http_status=400)
                eff_ttl = int(ttl_seconds)
            else:
                eff_ttl = default_ttl_seconds()
            stored_ttl = eff_ttl if eff_ttl > 0 else None
            expires_at_str = (now + timedelta(seconds=eff_ttl)).isoformat() if eff_ttl > 0 else None

        meta = {
            "schema_version": CHUNK_SCHEMA_VERSION,
            "task_id": tid,
            "chunk_id": cid,
            "chunk_type": ct,
            "tenant_id": ten,
            "created_at": now.isoformat(),
            "size_bytes": len(content_bytes),
            "content_hash": hashlib.sha256(content_bytes).hexdigest(),
            "retention": ret,
            "ref_count": 0,
            "ttl_seconds": stored_ttl,
            "expires_at": expires_at_str,
        }
        (cdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        (cdir / "content.json").write_bytes(content_bytes)
        _m.record_write_ok()
        return cid
    except ChunkStoreError:
        _m.record_write_fail()
        raise


def read_chunk(
    task_id: str,
    chunk_id: str,
    *,
    expect_tenant_id: str | None = None,
    deny_tenant_mismatch: bool = True,
    require_tenant_when_bound: bool = False,
) -> dict[str, Any] | None:
    cdir = _chunk_dir(task_id, chunk_id)
    meta_path = cdir / "meta.json"
    content_path = cdir / "content.json"
    if not meta_path.is_file() or not content_path.is_file():
        _m.record_read_miss()
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    if _is_gc_eligible(meta, now):
        try:
            _rmtree_chunk(cdir)
            _m.record_lazy_ttl_delete()
        except OSError:
            pass
        _m.record_read_miss()
        return None

    content = json.loads(content_path.read_text(encoding="utf-8"))
    out_cid = meta.get("chunk_id") or chunk_id
    issue = _tenant_issue(
        meta,
        expect_tenant_id,
        require_tenant_when_bound=require_tenant_when_bound,
    )
    if issue:
        _m.record_read_denied_tenant()
        if deny_tenant_mismatch:
            msg = (
                "X-Tenant-Id header required for this chunk"
                if issue == "CHUNK_TENANT_REQUIRED"
                else "tenant_id does not match chunk owner"
            )
            raise ChunkStoreError(
                issue,
                msg,
                http_status=403,
                details={"chunk_id": out_cid},
            )
        return None
    _m.record_read_ok()
    return {"chunk_id": out_cid, "meta": meta, "content": content}


def read_chunks_batch(
    task_id: str,
    chunk_ids: list[str],
    *,
    expect_tenant_id: str | None = None,
    require_tenant_when_bound: bool = False,
) -> dict[str, dict[str, Any] | None]:
    """批量读取；非法 id、缺失、租户问题均对应 null。"""
    _m.record_batch_get()
    out: dict[str, dict[str, Any] | None] = {}
    for raw in chunk_ids:
        try:
            cid = _validate_chunk_id(raw.strip())
        except ChunkStoreError:
            out[raw] = None
            continue
        try:
            rec = read_chunk(
                task_id,
                cid,
                expect_tenant_id=expect_tenant_id,
                deny_tenant_mismatch=False,
                require_tenant_when_bound=require_tenant_when_bound,
            )
        except ChunkStoreError:
            out[cid] = None
            continue
        out[cid] = rec
    return out


def adjust_chunk_ref(task_id: str, chunk_id: str, delta: int) -> int:
    """调整 ref_count，返回新值。chunk 不存在则 CHUNK_NOT_FOUND。"""
    lim = max_ref_delta_abs()
    if abs(int(delta)) > lim:
        raise ChunkStoreError(
            "CHUNK_REF_DELTA_TOO_LARGE",
            f"ref delta abs must be <= {lim}",
            http_status=400,
            details={"max_abs": lim},
        )
    if delta == 0:
        cdir = _chunk_dir(task_id, chunk_id)
        meta_path = cdir / "meta.json"
        if not meta_path.is_file():
            raise ChunkStoreError("CHUNK_NOT_FOUND", "chunk not found", http_status=404, details={"chunk_id": chunk_id})
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return _meta_ref_count(meta)

    cdir = _chunk_dir(task_id, chunk_id)
    meta_path = cdir / "meta.json"
    if not meta_path.is_file():
        raise ChunkStoreError("CHUNK_NOT_FOUND", "chunk not found", http_status=404, details={"chunk_id": chunk_id})
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    new_rc = max(0, _meta_ref_count(meta) + int(delta))
    meta["ref_count"] = new_rc
    _persist_meta(cdir, meta)
    return new_rc


def set_chunk_retention(task_id: str, chunk_id: str, retention: str) -> None:
    """切换生命周期档位；切回 ephemeral 时按当前时间与 ttl_seconds/default_ttl 重写 expires_at。"""
    ret = _validate_retention(retention)
    cdir = _chunk_dir(task_id, chunk_id)
    meta_path = cdir / "meta.json"
    if not meta_path.is_file():
        raise ChunkStoreError("CHUNK_NOT_FOUND", "chunk not found", http_status=404, details={"chunk_id": chunk_id})
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["retention"] = ret
    now = datetime.now(timezone.utc)
    if ret == "ephemeral":
        ttl_s = meta.get("ttl_seconds")
        if ttl_s is None:
            eff = default_ttl_seconds()
        else:
            try:
                eff = int(ttl_s)
            except (TypeError, ValueError):
                eff = default_ttl_seconds()
        meta["ttl_seconds"] = eff if eff > 0 else None
        meta["expires_at"] = (now + timedelta(seconds=eff)).isoformat() if eff > 0 else None
    else:
        meta["ttl_seconds"] = None
        meta["expires_at"] = None
    _persist_meta(cdir, meta)


def gc_sweep_task(task_id: str, *, record_metrics: bool = True) -> dict[str, Any]:
    """同步扫描单任务下 chunk，删除满足 TTL+无引用+ephemeral 的目录。"""
    now = datetime.now(timezone.utc)
    deleted: list[str] = []
    errors: list[dict[str, Any]] = []
    base = _task_chunks_dir(task_id)
    if not base.is_dir():
        if record_metrics:
            _m.record_gc_sweep_run(0)
        return {"task_id": task_id, "deleted": deleted, "deleted_count": 0, "errors": errors}

    for sub in sorted(base.iterdir()):
        if not sub.is_dir():
            continue
        meta_path = sub / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not _is_gc_eligible(meta, now):
            continue
        cid = meta.get("chunk_id") or sub.name
        try:
            _rmtree_chunk(sub)
            deleted.append(str(cid))
        except OSError as e:
            errors.append({"chunk_id": str(cid), "error": str(e)})

    if record_metrics:
        _m.record_gc_sweep_run(len(deleted))
    return {"task_id": task_id, "deleted": deleted, "deleted_count": len(deleted), "errors": errors}


def gc_sweep_all_tasks() -> dict[str, Any]:
    """扫描 WORKSPACE_ROOT 下所有含 chunks/ 的任务目录。"""
    root = _workspace_root()
    agg_deleted: list[str] = []
    per_task: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not root.is_dir():
        return {"deleted": agg_deleted, "deleted_count": 0, "tasks": per_task, "errors": errors}

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        chunks = child / "chunks"
        if not chunks.is_dir():
            continue
        tid = child.name
        try:
            r = gc_sweep_task(tid, record_metrics=False)
        except ChunkStoreError:
            continue
        per_task.append({"task_id": tid, "deleted_count": r["deleted_count"]})
        agg_deleted.extend(r["deleted"])
        errors.extend(r.get("errors") or [])

    _m.record_gc_sweep_run(len(agg_deleted))
    return {
        "deleted": agg_deleted,
        "deleted_count": len(agg_deleted),
        "tasks": per_task,
        "errors": errors,
    }
