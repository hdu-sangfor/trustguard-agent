from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.domain.models import SupervisorState
from app.workflows.registry import workflow_registry


_adapter = workflow_registry.resolve("pentest", "").adapter


def _activity(state: SupervisorState, kind: str, title: str, detail: str, status: str = "done") -> list[dict[str, Any]]:
    current = list(state.get("activities") or [])
    current.append({
        "id": f"step-{len(current) + 1}",
        "kind": kind,
        "title": title,
        "detail": detail,
        "status": status,
    })
    return current


def _parse_node(state: SupervisorState, adapter) -> SupervisorState:
    return {
        **state,
        "draft": adapter.build_draft(state.get("message") or ""),
        "activities": _activity(
            state,
            "analysis",
            "理解任务意图",
            "提取目标、测试重点、运行时长和风险偏好；不会展示模型私有思维链。",
        ),
    }


def parse_node(state: SupervisorState) -> SupervisorState:
    return _parse_node(state, _adapter)


def _validate_node(state: SupervisorState, adapter) -> SupervisorState:
    draft = state.get("draft") or {}
    missing, warnings = adapter.validate(draft)
    activities = _activity(
        state,
        "guard",
        "检查目标与安全边界",
        "验证目标格式，并将利用/破坏性请求标记为需要额外确认。",
    )
    if missing:
        activities = _activity(state | {"activities": activities}, "result", "需要补充信息", "缺少：" + "、".join(missing), "blocked")
    else:
        activities = _activity(state | {"activities": activities}, "result", "生成任务草稿", "草稿已准备好，等待你确认后创建任务。")
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
