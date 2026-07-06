from __future__ import annotations

from typing import Any, Dict, Tuple

from pydantic import ValidationError

from app.plan_models import (
    CompiledInstruction,
    PlanErrorCode,
    PlanErrorEnvelope,
    PlanItem,
    PlanList,
    PlanSchemaVersion,
)

JsonDict = Dict[str, Any]


def _build_error_envelope(
    code: PlanErrorCode,
    message: str,
    *,
    details: JsonDict | None = None,
) -> PlanErrorEnvelope:
    return PlanErrorEnvelope(
        schema_version=PlanSchemaVersion.V1,
        code=code,
        message=message,
        details=details or {},
    )


def validate_plan_item(payload: JsonDict) -> Tuple[bool, PlanErrorEnvelope | None]:
    """
    对 PlanItem 进行结构化校验。

    返回 (ok, error_envelope)：
    - ok=True 表示 payload 可被解析为合法 PlanItem；
    - ok=False 时 error_envelope 不为 None，包含稳定错误码与机读 details。
    """
    try:
        PlanItem.model_validate(payload)
        return True, None
    except ValidationError as exc:
        return False, _build_error_envelope(
            code=PlanErrorCode.INVALID_PLAN_ITEM,
            message="PlanItem validation failed",
            details={"errors": exc.errors()},
        )


def validate_plan_list(payload: JsonDict) -> Tuple[bool, PlanErrorEnvelope | None]:
    """
    对 PlanList 进行结构化校验。
    """
    try:
        PlanList.model_validate(payload)
        return True, None
    except ValidationError as exc:
        return False, _build_error_envelope(
            code=PlanErrorCode.INVALID_PLAN_LIST,
            message="PlanList validation failed",
            details={"errors": exc.errors()},
        )


def plan_validation_error_trace_fields(envelope: PlanErrorEnvelope) -> JsonDict:
    """
    契约/Plan 校验失败时并入 TraceEvent.payload 的推荐字段（与 nf-structured-error-envelope 一致）。
    """
    from app.structured_error_envelope import plan_error_envelope_to_client_dict

    return {"structured_error": plan_error_envelope_to_client_dict(envelope)}


def validate_compiled_instruction(
    payload: JsonDict,
) -> Tuple[bool, PlanErrorEnvelope | None]:
    """
    对 CompiledInstruction 进行结构化校验。
    """
    try:
        CompiledInstruction.model_validate(payload)
        return True, None
    except ValidationError as exc:
        return False, _build_error_envelope(
            code=PlanErrorCode.COMPILATION_FAILED,
            message="CompiledInstruction validation failed",
            details={"errors": exc.errors()},
        )

