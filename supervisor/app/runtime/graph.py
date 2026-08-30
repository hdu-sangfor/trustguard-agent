from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.domain.models import SupervisorState
from app.workflows.registry import workflow_registry


_adapter = workflow_registry.resolve("pentest", "").adapter


def _activity(state: SupervisorState, kind: str, title: str, detail: str, status: str = "done") -> list[dict[str, Any]]:
    current = list(state.get("activities") or [])
    current.append(_activity_payload(f"step-{len(current) + 1}", kind, title, detail, status))
    return current


def _activity_payload(step_id: str, kind: str, title: str, detail: str, status: str) -> dict[str, Any]:
    return {
        "id": step_id,
        "kind": kind,
        "title": title,
        "detail": detail,
        "status": status,
    }


def _workflow_copy(adapter) -> dict[str, str]:
    is_triage = adapter.workflow_id == "alert_triage"
    return {
        "parse_title": "理解告警研判意图" if is_triage else "理解任务意图",
        "parse_detail": (
            "提取告警 UUID、场景标记和 RAG 偏好；不会展示模型私有思维链。"
            if is_triage
            else "提取目标、测试重点、运行时长和风险偏好；不会展示模型私有思维链。"
        ),
        "parse_running": (
            "正在提取告警 UUID、场景标记和 RAG 偏好。"
            if is_triage
            else "正在提取目标范围、运行时长和风险偏好。"
        ),
        "guard_title": "检查告警标识与研判边界" if is_triage else "检查目标与安全边界",
        "guard_detail": (
            "验证告警 UUID，并确认该工作流只查询证据和给出人工处置建议。"
            if is_triage
            else "验证目标格式，并将利用/破坏性请求标记为需要额外确认。"
        ),
        "guard_running": (
            "正在验证告警 UUID 和研判边界。"
            if is_triage
            else "正在验证目标格式、授权范围和风险边界。"
        ),
        "draft_title": "生成研判任务草稿" if is_triage else "生成任务草稿",
        "draft_running": "正在整理可确认的任务草稿。",
    }


def _parse_node(state: SupervisorState, adapter) -> SupervisorState:
    copy = _workflow_copy(adapter)
    return {
        **state,
        "draft": adapter.build_draft(state.get("message") or ""),
        "activities": _activity(
            state,
            "analysis",
            copy["parse_title"],
            copy["parse_detail"],
        ),
    }


def parse_node(state: SupervisorState) -> SupervisorState:
    return _parse_node(state, _adapter)


def _validate_node(state: SupervisorState, adapter) -> SupervisorState:
    copy = _workflow_copy(adapter)
    draft = state.get("draft") or {}
    missing, warnings = adapter.validate(draft)
    activities = _activity(
        state,
        "guard",
        copy["guard_title"],
        copy["guard_detail"],
    )
    if missing:
        activities = _activity(state | {"activities": activities}, "result", "需要补充信息", "缺少：" + "、".join(missing), "blocked")
    else:
        activities = _activity(
            state | {"activities": activities},
            "result",
            copy["draft_title"],
            "草稿已准备好，等待你确认后创建任务。",
        )
    return {**state, "missing_fields": missing, "warnings": warnings, "activities": activities, "status": "NEEDS_CLARIFICATION" if missing else "NEEDS_CONFIRMATION"}


def validate_node(state: SupervisorState) -> SupervisorState:
    return _validate_node(state, _adapter)


def route_after_validate(state: SupervisorState) -> Literal["clarify", "confirm"]:
    return "clarify" if state.get("missing_fields") else "confirm"


def build_graph(adapter=None):
    selected = adapter or _adapter
    graph = StateGraph(SupervisorState)
    graph.add_node("parse", lambda state: _parse_node(state, selected))
    graph.add_node("validate", lambda state: _validate_node(state, selected))
    graph.add_node("clarify", lambda state: state)
    graph.add_node("confirm", lambda state: state)
    graph.set_entry_point("parse")
    graph.add_edge("parse", "validate")
    graph.add_conditional_edges("validate", route_after_validate, {"clarify": "clarify", "confirm": "confirm"})
    graph.add_edge("clarify", END)
    graph.add_edge("confirm", END)
    return graph.compile()


def run_graph(message: str, workflow_id: str = "pentest") -> SupervisorState:
    adapter = workflow_registry.resolve(workflow_id, message).adapter
    return build_graph(adapter).invoke({"message": message, "activities": []})


def run_graph_with_progress(
    message: str,
    workflow_id: str = "pentest",
    on_activity: Callable[[dict[str, Any]], None] | None = None,
) -> SupervisorState:
    adapter = workflow_registry.resolve(workflow_id, message).adapter
    copy = _workflow_copy(adapter)
    state: SupervisorState = {"message": message, "activities": []}

    if on_activity:
        on_activity(_activity_payload("step-1", "analysis", copy["parse_title"], copy["parse_running"], "running"))
    state = _parse_node(state, adapter)
    if on_activity:
        on_activity(state["activities"][-1])

    if on_activity:
        on_activity(_activity_payload("step-2", "guard", copy["guard_title"], copy["guard_running"], "running"))
    draft = state.get("draft") or {}
    missing, warnings = adapter.validate(draft)
    activities = _activity(state, "guard", copy["guard_title"], copy["guard_detail"])
    if on_activity:
        on_activity(activities[-1])

    if missing:
        activities = _activity(state | {"activities": activities}, "result", "需要补充信息", "缺少：" + "、".join(missing), "blocked")
        if on_activity:
            on_activity(activities[-1])
    else:
        if on_activity:
            on_activity(_activity_payload("step-3", "result", copy["draft_title"], copy["draft_running"], "running"))
        activities = _activity(
            state | {"activities": activities},
            "result",
            copy["draft_title"],
            "草稿已准备好，等待你确认后创建任务。",
        )
        if on_activity:
            on_activity(activities[-1])
    return {
        **state,
        "missing_fields": missing,
        "warnings": warnings,
        "activities": activities,
        "status": "NEEDS_CLARIFICATION" if missing else "NEEDS_CONFIRMATION",
    }
