"""
REPORT 阶段覆盖检查：基于覆盖矩阵（及可选 TodoList/tg_trace_events）列出已尝试与未尝试组合，并给出未覆盖说明。

Plan 四：未完全覆盖的部分在 REPORT 摘要中明确说明原因（工具暂缺/时间限制等）。
"""
from __future__ import annotations

from typing import Any, List

from app.clients.executor_client import fetch_skills_for_phase
from app.models import Phase


# 各阶段枚举，用于拉取「理论可用」技能并做差距分析
_PHASES_FOR_SKILLS = (
    Phase.RECON,
    Phase.THREAT_MODEL,
    Phase.VULN_SCAN,
    Phase.EXPLOIT,
)
_MAX_GAP_SAMPLE = 30


async def compute_report_coverage_gaps(
    coverage_attempted: List[dict[str, Any]],
) -> dict[str, Any]:
    """
    根据本次 run 的 coverage_attempted 与执行器各阶段可用 skill，计算已尝试 vs 理论可尝试的差距。

    返回结构：
    - attempted_count: 已尝试的 (target, skill_id) 数量
    - gap_count: 未尝试但理论可用的组合数
    - gap_sample: 未覆盖组合的抽样（最多 MAX_GAP_SAMPLE 条），每项 {"target": str, "skill_id": str}
    - uncovered_note: 未覆盖原因说明（固定模板，便于报告与审计）
    """
    attempted_list = coverage_attempted or []
    attempted_set = set(
        (str(c.get("target") or "").strip(), str(c.get("skill_id") or "").strip())
        for c in attempted_list
        if c.get("target") or c.get("skill_id")
    )
    targets = list({t for t, _ in attempted_set})
    if not targets:
        return {
            "attempted_count": 0,
            "gap_count": 0,
            "gap_sample": [],
            "uncovered_note": "本任务未执行任何技能，无覆盖矩阵；可能为仅决策未执行或未启用执行器。",
        }

    all_skills: set[str] = set()
    for phase in _PHASES_FOR_SKILLS:
        try:
            ids = await fetch_skills_for_phase(phase.value)
            all_skills.update(ids or [])
        except Exception:
            pass

    if not all_skills:
        return {
            "attempted_count": len(attempted_set),
            "gap_count": 0,
            "gap_sample": [],
            "uncovered_note": "无法获取执行器技能列表，仅上报已尝试组合；未覆盖原因可能为工具暂缺或执行器不可用。",
        }

    theoretical = {(t, s) for t in targets for s in all_skills}
    gaps = theoretical - attempted_set
    gap_list = [{"target": t, "skill_id": s} for t, s in sorted(gaps)[:_MAX_GAP_SAMPLE]]

    note = (
        "未执行组合可能因工具选择、时间限制、阶段顺序或 LLM 决策未选用等原因未覆盖；"
        "详见 gap_sample。若需更细原因可结合 TodoList 与 tg_trace_events 做后续分析。"
    )
    if not gaps:
        note = "当前目标×技能理论组合均已尝试或仅单目标单技能，无未覆盖组合。"

    return {
        "attempted_count": len(attempted_set),
        "gap_count": len(gaps),
        "gap_sample": gap_list,
        "uncovered_note": note,
    }
