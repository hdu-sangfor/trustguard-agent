"""phase_capability_policy: skill-only execution kind policy."""

from __future__ import annotations

import json

import pytest

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.core.phase_capability_policy import (  # type: ignore[import]
    PHASE_CAPABILITY_REGISTRY,
    CapabilityKit,
    PhasePolicy,
    effective_plan_item_execution_kind,
    policy_for_phase,
)
from app.enums import Phase  # type: ignore[import]


def test_phase_policy_registry_covers_all_phases() -> None:
    for p in Phase:
        assert p in PHASE_CAPABILITY_REGISTRY
        row = PHASE_CAPABILITY_REGISTRY[p]
        assert row.phase == p.value
        assert isinstance(row.kit, CapabilityKit)


def test_phase_policy_json_roundtrip() -> None:
    pol = PhasePolicy(
        phase="RECON",
        kit=CapabilityKit(summary="x", default_execution_kind="skill"),
    )
    raw = pol.model_dump(mode="json")
    assert json.loads(json.dumps(raw)) == raw
    restored = PhasePolicy.model_validate(raw)
    assert restored.kit.default_execution_kind == "skill"


def test_effective_kind_metadata_rejects_agent_conflict() -> None:
    with pytest.raises(ValueError):
        effective_plan_item_execution_kind(
            skill_id="nmap",
            metadata={"execution_kind": "agent"},
            current_phase=Phase.RECON,
        )


def test_effective_kind_metadata_skill_for_nmap() -> None:
    assert (
        effective_plan_item_execution_kind(
            skill_id="nmap",
            metadata={"execution_kind": "skill"},
            current_phase=Phase.RECON,
        )
        == "skill"
    )


def test_effective_kind_phase_default_skill_for_httpx() -> None:
    assert (
        effective_plan_item_execution_kind(
            skill_id="httpx",
            metadata={},
            current_phase=Phase.VULN_SCAN,
        )
        == "skill"
    )


def test_effective_kind_defaults_to_skill_without_metadata() -> None:
    assert (
        effective_plan_item_execution_kind(
            skill_id="nmap",
            metadata={},
            current_phase=Phase.RECON,
        )
        == "skill"
    )


def test_policy_report_has_no_default_execution_kind() -> None:
    assert policy_for_phase(Phase.REPORT).kit.default_execution_kind is None
