from __future__ import annotations

import os
from typing import Any, Dict, List

from app.core.agent_tools import fingerprint_signals_present
from app.models import Phase


def bias_available_skills_for_decision(
    phase: Phase | None,
    available_skills: List[str],
    target_context: Dict[str, Any] | None,
) -> List[str]:
    """
    软调度偏好：VULN_SCAN 且已有指纹/whatweb 信号时，将 web-vuln-pipeline 置于列表首位。
    不改变允许集合，仅调整顺序，降低 LLM 忽略宏管道的概率。
    """
    pref = "web-vuln-pipeline"
    if phase != Phase.VULN_SCAN or pref not in available_skills:
        return list(available_skills)
    if not fingerprint_signals_present(target_context or {}):
        return list(available_skills)
    return [pref] + [s for s in available_skills if s != pref]


def ensure_baidu_search_offered(phase_value: str, skills: List[str]) -> List[str]:
    """
    执行器短暂不可达或列表异常时，决策上下文可能缺少 baidu-search。
    在 RECON / THREAT_MODEL 阶段将该 skill 补进 available_skill_ids（ORCHESTRATOR_ALWAYS_OFFER_BAIDU_SEARCH=0 可关）。
    """
    if (os.getenv("ORCHESTRATOR_ALWAYS_OFFER_BAIDU_SEARCH", "1") or "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return list(skills)
    ph = (phase_value or "").upper()
    if ph not in ("RECON", "THREAT_MODEL"):
        return list(skills)
    out = list(skills)
    if "baidu-search" not in out:
        out.append("baidu-search")
    return out
