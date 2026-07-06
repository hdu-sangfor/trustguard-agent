"""Shared enums to avoid circular imports between app.models and app.core.task_store."""
from enum import Enum


_PHASE_ALIASES: dict[str, str] = {
    # LLM / 人工常写的变体 → 平台标准值
    "VULNERABILITY_SCAN": "VULN_SCAN",
    "VULN-SCAN": "VULN_SCAN",
    "THREATMODEL": "THREAT_MODEL",
    "THREAT-MODEL": "THREAT_MODEL",
    "RECONNAISSANCE": "RECON",
    "POST_EXPLOIT": "EXPLOIT",
    "POST-EXPLOIT": "EXPLOIT",
    "FINAL_REPORT": "REPORT",
}


def coerce_phase_from_llm(raw: str | None) -> "Phase":
    """
    将决策引擎返回的 next_phase 字符串规范化为 Phase。
    失败时抛出 ValueError，供状态机转为 400 并带上原文便于审计。
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty next_phase")
    normalized = s.upper().replace(" ", "_").replace("-", "_")
    normalized = _PHASE_ALIASES.get(normalized, normalized)
    return Phase(normalized)


class Phase(str, Enum):
    """PTES 阶段映射到平台内部阶段。"""

    RECON = "RECON"
    THREAT_MODEL = "THREAT_MODEL"
    VULN_SCAN = "VULN_SCAN"
    EXPLOIT = "EXPLOIT"
    REPORT = "REPORT"
    DONE = "DONE"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DONE = "DONE"
    FAILED = "FAILED"
