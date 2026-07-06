"""
R8a：编排器进程内 SLI 计数（编译成功率、tick 时延与失败次数）。

供 GET /v1/orchestrator/sli/snapshot 与外部告警系统拉取；阈值在 orchestrator_sli_snapshot 中评估。
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Dict

_lock = Lock()
_instruction_compile_total = 0
_instruction_compile_ok = 0
_instruction_compile_fail = 0
_instruction_compile_fail_by_code: Dict[str, int] = {}
_instruction_compile_duration_ms_sum = 0.0
_instruction_compile_duration_ms_count = 0
_instruction_compile_duration_ms_max = 0.0
_instruction_compile_duration_bucket_counts: Dict[str, int] = {
    "le_50ms": 0,
    "le_200ms": 0,
    "le_1000ms": 0,
    "gt_1000ms": 0,
}

_tick_attempts_total = 0
_tick_attempts_failed = 0
_tick_duration_ms_sum = 0.0
_tick_duration_ms_count = 0
_tick_duration_ms_max = 0.0
_skill_exec_total = 0
_skill_exec_by_skill: Dict[str, int] = {}
_skill_exec_fail_by_skill: Dict[str, int] = {}
_skill_exec_timeout_by_skill: Dict[str, int] = {}

_kb_promotion_sweeps = 0
_kb_promotion_candidates = 0
_kb_promotion_ok = 0
_kb_promotion_fail = 0
_kb_promotion_rollback_skips = 0


def record_instruction_compile_result(ok: bool, error_code: str | None, duration_ms: float | None = None) -> None:
    global _instruction_compile_total, _instruction_compile_ok, _instruction_compile_fail
    global _instruction_compile_fail_by_code
    global _instruction_compile_duration_ms_sum, _instruction_compile_duration_ms_count, _instruction_compile_duration_ms_max
    global _instruction_compile_duration_bucket_counts
    with _lock:
        _instruction_compile_total += 1
        if ok:
            _instruction_compile_ok += 1
        else:
            _instruction_compile_fail += 1
            if error_code:
                k = str(error_code).strip() or "UNKNOWN"
                _instruction_compile_fail_by_code[k] = _instruction_compile_fail_by_code.get(k, 0) + 1
        if duration_ms is not None and duration_ms >= 0:
            _instruction_compile_duration_ms_sum += float(duration_ms)
            _instruction_compile_duration_ms_count += 1
            if float(duration_ms) > _instruction_compile_duration_ms_max:
                _instruction_compile_duration_ms_max = float(duration_ms)
            if duration_ms <= 50:
                _instruction_compile_duration_bucket_counts["le_50ms"] += 1
            elif duration_ms <= 200:
                _instruction_compile_duration_bucket_counts["le_200ms"] += 1
            elif duration_ms <= 1000:
                _instruction_compile_duration_bucket_counts["le_1000ms"] += 1
            else:
                _instruction_compile_duration_bucket_counts["gt_1000ms"] += 1


def record_tick_attempt(*, ok: bool, duration_ms: float) -> None:
    global _tick_attempts_total, _tick_attempts_failed
    global _tick_duration_ms_sum, _tick_duration_ms_count, _tick_duration_ms_max
    with _lock:
        _tick_attempts_total += 1
        if not ok:
            _tick_attempts_failed += 1
        if ok and duration_ms >= 0:
            _tick_duration_ms_sum += duration_ms
            _tick_duration_ms_count += 1
            if duration_ms > _tick_duration_ms_max:
                _tick_duration_ms_max = duration_ms


def record_skill_execution_result(skill_id: str, status: str) -> None:
    global _skill_exec_total, _skill_exec_by_skill, _skill_exec_fail_by_skill, _skill_exec_timeout_by_skill
    sid = (skill_id or "").strip().lower() or "unknown"
    st = (status or "").strip().upper()
    with _lock:
        _skill_exec_total += 1
        _skill_exec_by_skill[sid] = _skill_exec_by_skill.get(sid, 0) + 1
        if st in ("FAILED", "TIMEOUT"):
            _skill_exec_fail_by_skill[sid] = _skill_exec_fail_by_skill.get(sid, 0) + 1
        if st == "TIMEOUT":
            _skill_exec_timeout_by_skill[sid] = _skill_exec_timeout_by_skill.get(sid, 0) + 1


def record_kb_experience_promotion_sweep(
    *,
    candidates: int,
    promoted_ok: int,
    promoted_fail: int,
    rollback_skips: int = 0,
) -> None:
    """kb-r4b：单次 sweep 的候选数与晋升成功/失败计数（供 SLI 与告警）。"""
    global _kb_promotion_sweeps, _kb_promotion_candidates, _kb_promotion_ok, _kb_promotion_fail
    global _kb_promotion_rollback_skips
    with _lock:
        _kb_promotion_sweeps += 1
        _kb_promotion_candidates += max(0, int(candidates))
        _kb_promotion_ok += max(0, int(promoted_ok))
        _kb_promotion_fail += max(0, int(promoted_fail))
        _kb_promotion_rollback_skips += max(0, int(rollback_skips))


def reset_kb_experience_promotion_metrics_for_tests() -> None:
    global _kb_promotion_sweeps, _kb_promotion_candidates, _kb_promotion_ok, _kb_promotion_fail
    global _kb_promotion_rollback_skips
    with _lock:
        _kb_promotion_sweeps = 0
        _kb_promotion_candidates = 0
        _kb_promotion_ok = 0
        _kb_promotion_fail = 0
        _kb_promotion_rollback_skips = 0


def snapshot_counters() -> dict[str, Any]:
    with _lock:
        ct = _instruction_compile_total
        ok = _instruction_compile_ok
        fail = _instruction_compile_fail
        cdc = _instruction_compile_duration_ms_count
        csum = _instruction_compile_duration_ms_sum
        cmax = _instruction_compile_duration_ms_max
        tt = _tick_attempts_total
        tf = _tick_attempts_failed
        tdc = _tick_duration_ms_count
        tsum = _tick_duration_ms_sum
        tmax = _tick_duration_ms_max
        sext = _skill_exec_total
        sexec = dict(_skill_exec_by_skill)
        sfail = dict(_skill_exec_fail_by_skill)
        stimeout = dict(_skill_exec_timeout_by_skill)
        fmap = dict(_instruction_compile_fail_by_code)
        cbkt = dict(_instruction_compile_duration_bucket_counts)
        kbp_sw = _kb_promotion_sweeps
        kbp_ca = _kb_promotion_candidates
        kbp_ok = _kb_promotion_ok
        kbp_fl = _kb_promotion_fail
        kbp_rb = _kb_promotion_rollback_skips
    compile_fail_rate: float | None
    if ct <= 0:
        compile_fail_rate = None
    else:
        compile_fail_rate = round(fail / ct, 6)
    tick_fail_rate: float | None
    if tt <= 0:
        tick_fail_rate = None
    else:
        tick_fail_rate = round(tf / tt, 6)
    tick_avg_ms: float | None
    if tdc <= 0:
        tick_avg_ms = None
    else:
        tick_avg_ms = round(tsum / tdc, 3)
    return {
        "instruction_compile": {
            "compile_total": ct,
            "compile_ok": ok,
            "compile_fail": fail,
            "compile_fail_rate": compile_fail_rate,
            "compile_fail_by_code": fmap,
            "compile_duration_ms_avg": (round(csum / cdc, 3) if cdc > 0 else None),
            "compile_duration_ms_max": (round(cmax, 3) if cmax > 0 else 0.0),
            "compile_duration_samples": cdc,
            "compile_duration_bucket_counts": cbkt,
        },
        "orchestrator_tick": {
            "tick_attempts_total": tt,
            "tick_attempts_failed": tf,
            "tick_fail_rate": tick_fail_rate,
            "tick_duration_ms_avg": tick_avg_ms,
            "tick_duration_ms_max": round(tmax, 3) if tmax > 0 else 0.0,
            "tick_duration_samples": tdc,
        },
        "skill_execution": {
            "execution_total": sext,
            "execution_by_skill": sexec,
            "execution_fail_by_skill": sfail,
            "execution_timeout_by_skill": stimeout,
        },
        "kb_experience_promotion": {
            "promotion_sweeps": kbp_sw,
            "promotion_candidates_total": kbp_ca,
            "promoted_ok_total": kbp_ok,
            "promoted_fail_total": kbp_fl,
            "promotion_rollback_skips_total": kbp_rb,
            "promotion_fail_rate": (
                round(kbp_fl / (kbp_ok + kbp_fl), 6)
                if (kbp_ok + kbp_fl) > 0
                else None
            ),
        },
    }
