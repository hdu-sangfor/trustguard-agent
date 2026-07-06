"""
工件旁路嗅探池（D-05）：从子进程 stderr 逐行解析 `[__SYS_ARTIFACT__]::` 协议，去重累积。
线程安全：供 Daemon 异步读 stderr 时调用 `ingest_line`。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.micro_executor.protocol import parse_artifact_notice_line


@dataclass
class SniffedArtifactRecord:
    """单条嗅探记录（与协议 JSON 字段对齐）。"""

    artifact_ref: str
    skill_id: str = ""
    type_tag: str = ""
    request_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class ArtifactSniffPool:
    """
    会话级池：同一 MQ request_id 一次执行共用；终态 finish 时用 `artifact_refs_ordered()`。
    """

    def __init__(self, *, request_id: str = "") -> None:
        self._request_id = (request_id or "").strip()
        self._lock = threading.Lock()
        self._order: list[str] = []
        self._by_ref: dict[str, SniffedArtifactRecord] = {}

    def ingest_line(self, line: str) -> bool:
        """
        解析一行 stderr；若为标准工件通知则入库并返回 True，否则 False。
        """
        parsed = parse_artifact_notice_line(line)
        if not parsed:
            return False
        ref = str(parsed.get("artifact_ref", "")).strip()
        if not ref:
            return False
        rec = SniffedArtifactRecord(
            artifact_ref=ref,
            skill_id=str(parsed.get("skill_id", "") or ""),
            type_tag=str(parsed.get("type_tag", "") or ""),
            request_id=str(parsed.get("request_id", "") or "") or self._request_id,
            raw=dict(parsed),
        )
        with self._lock:
            if ref not in self._by_ref:
                self._by_ref[ref] = rec
                self._order.append(ref)
            else:
                # 同 ref 再次上报：合并非空字段（后者不覆盖已有 skill_id 除非原为空）
                old = self._by_ref[ref]
                if not old.skill_id and rec.skill_id:
                    old.skill_id = rec.skill_id
                if not old.type_tag and rec.type_tag:
                    old.type_tag = rec.type_tag
                if not old.request_id and rec.request_id:
                    old.request_id = rec.request_id
        return True

    def records_ordered(self) -> list[SniffedArtifactRecord]:
        with self._lock:
            return [self._by_ref[r] for r in self._order if r in self._by_ref]

    def artifact_refs_ordered(self) -> list[str]:
        with self._lock:
            return list(self._order)

    def __len__(self) -> int:
        with self._lock:
            return len(self._order)
