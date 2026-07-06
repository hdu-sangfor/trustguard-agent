"""Pre-flight 策略引擎共享上下文（可变 ctx 与任务态）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import TaskState


@dataclass
class PreflightContext:
    """单次 tick 内对 target_context 的可变引用；dispatcher 可写入 run_id，nuclei 随后可读。"""

    state: TaskState
    ctx: dict[str, Any]
    iml: int
    seeds: list[str]
    run_id_key: str
