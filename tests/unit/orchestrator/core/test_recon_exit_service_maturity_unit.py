"""
单元测试：RECON 退出条件放宽 — 支持 iml>=2 作为备选通道。

实地证据 (task-c90db15cf652498eab05c5033612f1d1, thinkphp 5.0.23):
- 首页空白，katana 爬不到链接 → clustered_targets_preview=[]
- ehole 无匹配模板 → asset_path_profile=None
- 但 L1_connectivity=True, L2_service_banner=True → iml=2
- LLM 12 次要求 advance_phase=true,next_phase=VULN_SCAN，
  全部被 PHASE_GATE_BLOCKED("recon exit criteria not met")
- 任务在 RECON 死循环 13 分钟

修复：iml>=2 视为"服务级成熟"，可作为 _recon_exit_ok 的第二通道。
"""
import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


def _state(ctx: dict):
    from app.models import TaskState, Phase

    s = TaskState(task_id="t-recon-exit", name="n", target="http://127.0.0.1:8080")
    s.current_phase = Phase.RECON
    s.target_context.update(ctx or {})
    return s


def test_recon_exit_allowed_when_iml_ge_2_without_crawl():
    """iml>=2 时，即使 clustered_targets_preview 和 asset_path_profile 都为空，也应允许退出。"""
    from app.enums import Phase
    from app.core.phase_transition_guard import guard_next_phase

    s = _state(
        {
            "clustered_targets_preview": [],
            "asset_path_profile": None,
            "information_maturity_level": 2,
        }
    )
    d = guard_next_phase(s, Phase.VULN_SCAN)
    assert d.allow, f"expected allow, got missing={d.missing} reason={d.reason}"


def test_recon_exit_allowed_via_crawl_channel_even_when_iml_low():
    """回归：原爬虫通道仍然有效 — iml=1 但有 clustered_targets_preview 时允许退出。"""
    from app.enums import Phase
    from app.core.phase_transition_guard import guard_next_phase

    s = _state(
        {
            "clustered_targets_preview": ["http://127.0.0.1:8080/a", "http://127.0.0.1:8080/b"],
            "asset_path_profile": None,
            "information_maturity_level": 1,
        }
    )
    d = guard_next_phase(s, Phase.VULN_SCAN)
    assert d.allow


def test_recon_exit_blocked_when_iml_lt_2_and_no_crawl():
    """iml=1 + 无爬虫/画像 → 仍然阻断（保留最低信息门槛）。"""
    from app.enums import Phase
    from app.core.phase_transition_guard import guard_next_phase

    s = _state(
        {
            "clustered_targets_preview": [],
            "asset_path_profile": None,
            "information_maturity_level": 1,
        }
    )
    d = guard_next_phase(s, Phase.VULN_SCAN)
    assert not d.allow
    assert "clustered_targets_preview|asset_path_profile|information_maturity_level>=2" in d.missing


def test_recon_exit_still_requires_iml_ge_1_hard_floor():
    """iml=0（连最基础连接都没确认）时，无论其他字段如何都不允许退出。"""
    from app.enums import Phase
    from app.core.phase_transition_guard import guard_next_phase

    s = _state(
        {
            "clustered_targets_preview": [],
            "asset_path_profile": None,
            "information_maturity_level": 0,
        }
    )
    d = guard_next_phase(s, Phase.VULN_SCAN)
    assert not d.allow
    assert "information_maturity_level>=1" in d.missing
