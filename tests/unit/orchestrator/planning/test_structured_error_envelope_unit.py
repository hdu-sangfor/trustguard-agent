from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.plan_models import PlanErrorCode, PlanErrorEnvelope, PlanSchemaVersion
from app.structured_error_envelope import (
    plan_error_envelope_to_client_dict,
    structured_error_for_skill_dispatch,
)


def test_plan_error_envelope_jsonable():
    env = PlanErrorEnvelope(
        schema_version=PlanSchemaVersion.V1,
        code=PlanErrorCode.INVALID_PLAN_ITEM,
        message="bad item",
        details={"errors": [{"loc": ("x",), "msg": "missing"}]},
    )
    d = plan_error_envelope_to_client_dict(env)
    assert d["code"] == "INVALID_PLAN_ITEM"
    assert d["schema_version"] == "plan-v1"
    assert "errors" in d["details"]


def test_structured_error_dispatch_mapping():
    se = structured_error_for_skill_dispatch(
        status="SKIPPED_EXECUTOR_DISABLED",
        skill_id="nmap",
        inner_error="",
        file_missing=False,
    )
    assert se["code"] == "SKIPPED_EXECUTOR_DISABLED"
    se2 = structured_error_for_skill_dispatch(
        status="FAILED",
        skill_id="katana",
        inner_error="MQ_POLL_TIMEOUT",
        file_missing=False,
    )
    assert se2["code"] == "MQ_POLL_TIMEOUT"
    se3 = structured_error_for_skill_dispatch(
        status="SUCCESS",
        skill_id="x",
        inner_error="",
        file_missing=True,
    )
    assert se3["code"] == "ARTIFACT_FILE_MISSING"
