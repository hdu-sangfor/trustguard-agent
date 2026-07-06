"""Plan feature flags."""
from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.plan_feature_flags import (  # type: ignore[import]
    orch_legacy_decision_path,
    orch_plan_kit_anchor_skill_enabled,
    orch_plan_mode_enabled,
)


def test_plan_mode_on_by_default_empty_env_r8c():
    env = {}
    assert orch_plan_mode_enabled(env) is True
    assert orch_legacy_decision_path(env) is False


def test_plan_kit_anchor_skill_disabled_by_default():
    assert orch_plan_kit_anchor_skill_enabled({}) is False
    assert orch_plan_kit_anchor_skill_enabled({"ORCH_PLAN_KIT_ANCHOR_SKILL": ""}) is False


def test_plan_kit_anchor_skill_true_variants():
    for val in ("1", "true", "yes", "on", "TRUE"):
        assert orch_plan_kit_anchor_skill_enabled({"ORCH_PLAN_KIT_ANCHOR_SKILL": val}) is True
    assert orch_plan_kit_anchor_skill_enabled({"ORCH_PLAN_KIT_ANCHOR_SKILL": "0"}) is False


def test_plan_mode_on_true_variants():
    for val in ("true", "TRUE", "1", "yes", "on", " On "):
        assert orch_plan_mode_enabled({"ORCH_PLAN_MODE_ENABLED": val}) is True
        assert orch_legacy_decision_path({"ORCH_PLAN_MODE_ENABLED": val}) is False


def test_plan_mode_forced_on_ignores_falsey_env_variants():
    for val in ("false", "0", "no", "off", "maybe"):
        assert orch_plan_mode_enabled({"ORCH_PLAN_MODE_ENABLED": val}) is True
        assert orch_legacy_decision_path({"ORCH_PLAN_MODE_ENABLED": val}) is False


def test_plan_mode_empty_string_means_default_on():
    assert orch_plan_mode_enabled({"ORCH_PLAN_MODE_ENABLED": ""}) is True
    assert orch_legacy_decision_path({"ORCH_PLAN_MODE_ENABLED": ""}) is False


def test_plan_mode_reads_process_environ_when_none(monkeypatch):
    monkeypatch.setenv("ORCH_PLAN_MODE_ENABLED", "true")
    assert orch_plan_mode_enabled() is True
    monkeypatch.delenv("ORCH_PLAN_MODE_ENABLED", raising=False)
    assert orch_plan_mode_enabled() is True
