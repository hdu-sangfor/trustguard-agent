"""FinOps 解析与成本小函数单测。"""

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core import governance_cost  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_finops_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "ORCH_PHASE_COST_BUDGET_USD",
        "ORCH_FINOPS_INPUT_USD_PER_1M",
        "ORCH_FINOPS_OUTPUT_USD_PER_1M",
    ):
        monkeypatch.delenv(k, raising=False)


def test_parse_token_counts_anthropic_style() -> None:
    inp, out = governance_cost.parse_token_counts({"input_tokens": 100, "output_tokens": 50})
    assert inp == 100
    assert out == 50


def test_parse_token_counts_openai_style() -> None:
    inp, out = governance_cost.parse_token_counts({"prompt_tokens": 12, "completion_tokens": 34})
    assert inp == 12
    assert out == 34


def test_merge_usage_sources_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Er:
        usage = {"input_tokens": 1, "output_tokens": 2}

    u = governance_cost.merge_usage_sources(_Er(), {"usage": {"input_tokens": 9, "output_tokens": 9}})
    assert u == {"input_tokens": 1, "output_tokens": 2}


def test_cost_usd_for_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_FINOPS_INPUT_USD_PER_1M", "3.0")
    monkeypatch.setenv("ORCH_FINOPS_OUTPUT_USD_PER_1M", "15.0")
    # reload-ish: functions read env each call
    c = governance_cost.cost_usd_for_tokens(1_000_000, 1_000_000)
    assert abs(c - 18.0) < 1e-9


def test_phase_cost_budget_disabled_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_PHASE_COST_BUDGET_USD", raising=False)
    assert governance_cost.phase_cost_budget_usd_from_env() is None


def test_phase_cost_budget_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_PHASE_COST_BUDGET_USD", "12.5")
    assert governance_cost.phase_cost_budget_usd_from_env() == 12.5


def test_accumulate_finops_usage_mapping_into_state_mutates_tasklike() -> None:
    class _S:
        llm_input_tokens_total = 0
        llm_output_tokens_total = 0
        cumulative_cost_usd = 0.0

    st = _S()
    delta = governance_cost.accumulate_finops_usage_mapping_into_state(st, {"prompt_tokens": 10, "completion_tokens": 5})
    assert st.llm_input_tokens_total == 10
    assert st.llm_output_tokens_total == 5
    assert delta["delta_input_tokens"] == 10
    assert delta["delta_output_tokens"] == 5
