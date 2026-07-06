"""
单元测试：Fix D5 framework_detect — 从 ctx / state 自然语言证据识别 struts2 / spring / thinkphp。

实地背景：r3~r7 有 ~50% 的任务跳过 THREAT_MODEL（RECON→VULN_SCAN 直跳），
导致 framework_target 落到 GENERIC_WEB / CUSTOM_APP 而非具体框架。
D5 把识别能力提前到 RECON→VULN_SCAN 转场的 normalize_framework_unknown_if_needed 里。
"""
import os
import sys
from tests.paths import REPO_ROOT

_ORCH_ROOT = str(REPO_ROOT / "orchestrator")
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


class _FakeState:
    def __init__(self, ctx: dict, confirmed_facts: list | None = None, history_summary: str = ""):
        self.target_context = ctx
        self.confirmed_facts = confirmed_facts or []
        self.history_summary = history_summary


def test_detect_from_asset_path_profile_struts2():
    from app.core.framework_detect import detect_framework_from_context
    fw, ev = detect_framework_from_context({"asset_path_profile": {"stack_hint": "struts2_heavy"}})
    assert fw == "struts2"
    assert "asset_path_profile:struts2_heavy" in ev


def test_detect_from_asset_path_profile_php():
    from app.core.framework_detect import detect_framework_from_context
    fw, ev = detect_framework_from_context({"asset_path_profile": {"stack_hint": "php_heavy"}})
    assert fw == "php"


def test_detect_keyword_struts_in_tech_evidence():
    from app.core.framework_detect import detect_framework_from_context
    ctx = {"tech_stack_evidence": [{"signal": "Apache Struts 2.3 showcase"}]}
    fw, ev = detect_framework_from_context(ctx)
    assert fw == "struts2"
    assert any("struts" in e for e in ev)


def test_detect_struts2_from_http_enum_title_and_form_action():
    from app.core.framework_detect import detect_framework_from_context
    ctx = {
        "http-enum_title": "Struts2 Showcase - Fileupload sample",
        "http-enum_raw_preview": '<form action="/doUpload.action;jsessionid=abc" method="POST">',
    }
    fw, ev = detect_framework_from_context(ctx)
    assert fw == "struts2"
    assert any("struts" in e for e in ev)


def test_detect_url_suffix_action_implies_struts2():
    from app.core.framework_detect import detect_framework_from_context
    ctx = {"high_value_endpoints": ["http://host/ajax/bind.action"]}
    fw, ev = detect_framework_from_context(ctx)
    assert fw == "struts2"
    assert "url-suffix:.action" in ev


def test_detect_thinkphp_keyword():
    from app.core.framework_detect import detect_framework_from_context
    ctx = {"pipeline_tech_stack_evidence": ["ThinkPHP V1 detected"]}
    fw, ev = detect_framework_from_context(ctx)
    assert fw == "thinkphp"


def test_detect_spring_keyword():
    from app.core.framework_detect import detect_framework_from_context
    ctx = {"tech_stack_evidence": [{"text": "spring boot actuator /env exposed"}]}
    fw, ev = detect_framework_from_context(ctx)
    assert fw == "spring"


def test_detect_empty_ctx_returns_unknown():
    from app.core.framework_detect import detect_framework_from_context
    fw, ev = detect_framework_from_context({})
    assert fw == ""


def test_state_fallback_uses_confirmed_facts():
    from app.core.framework_detect import detect_framework_from_state_fallback
    st = _FakeState({}, confirmed_facts=["confirmed_cve: S2-057 struts2 RCE"])
    fw, ev = detect_framework_from_state_fallback(st)
    assert fw == "struts2"


def test_state_fallback_uses_history_summary():
    from app.core.framework_detect import detect_framework_from_state_fallback
    st = _FakeState({}, history_summary="[nuclei] thinkphp 5.0.23 captcha RCE confirmed")
    fw, ev = detect_framework_from_state_fallback(st)
    assert fw == "thinkphp"


def test_normalize_framework_bypass_recovers_specific_framework():
    """
    D5 核心断言：normalize_framework_unknown_if_needed 现在会先尝试识别具体框架，
    避免直接落到 GENERIC_WEB / CUSTOM_APP。
    """
    from app.models import TaskState, Phase
    from app.core.phase_transition_guard import normalize_framework_unknown_if_needed

    s = TaskState(task_id="t-d5", name="n", target="http://127.0.0.1:8080")
    s.current_phase = Phase.RECON
    s.target_context.update(
        {
            # 没有 framework_target，但爬虫里有 .action 路径 → 应识别为 struts2
            "high_value_endpoints": ["http://127.0.0.1:8080/ajax/bind.action"],
        }
    )
    result = normalize_framework_unknown_if_needed(s)
    assert result == "struts2"
    assert s.target_context.get("framework_target") == "struts2"
    assert s.target_context.get("framework_hint") == "struts2"


def test_normalize_framework_still_falls_back_when_nothing_identifiable():
    """
    回归：当证据为空时，仍然落到 GENERIC_WEB / CUSTOM_APP marker（保持原行为）。
    """
    from app.models import TaskState, Phase
    from app.core.phase_transition_guard import normalize_framework_unknown_if_needed

    s = TaskState(task_id="t-d5-fb", name="n", target="http://127.0.0.1:8080")
    s.current_phase = Phase.RECON
    # 无任何可识别证据
    result = normalize_framework_unknown_if_needed(s)
    assert result == "GENERIC_WEB"
    assert s.target_context.get("framework_target") == "GENERIC_WEB"


def test_normalize_framework_custom_app_when_asset_path_profile_present_but_no_framework():
    """asset_path_profile 存在但 stack_hint 无法识别 → CUSTOM_APP（非具体框架）。"""
    from app.models import TaskState, Phase
    from app.core.phase_transition_guard import normalize_framework_unknown_if_needed

    s = TaskState(task_id="t-d5-custom", name="n", target="http://127.0.0.1:8080")
    s.current_phase = Phase.RECON
    s.target_context.update(
        {
            "asset_path_profile": {"stack_hint": "unknown_proprietary"},
        }
    )
    result = normalize_framework_unknown_if_needed(s)
    assert result == "CUSTOM_APP"


def test_normalize_framework_preserves_existing_value():
    """回归：若 framework_target 已有值则不覆盖。"""
    from app.models import TaskState, Phase
    from app.core.phase_transition_guard import normalize_framework_unknown_if_needed

    s = TaskState(task_id="t-d5-preserve", name="n", target="http://127.0.0.1:8080")
    s.current_phase = Phase.RECON
    s.target_context.update({"framework_target": "spring"})
    result = normalize_framework_unknown_if_needed(s)
    assert result == "spring"
    assert s.target_context.get("framework_target") == "spring"
