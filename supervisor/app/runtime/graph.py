from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.domain.models import SupervisorState
from app.workflows.pentest.adapter import PentestWorkflowAdapter


_adapter = PentestWorkflowAdapter()


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


def parse_node(state: SupervisorState) -> SupervisorState:
    return {
        **state,
        "draft": _adapter.build_draft(state.get("message") or ""),
        "activities": _activity(
            state,
            "analysis",
            "理解任务意图",
            "提取目标、测试重点、运行时长和风险偏好；不会展示模型私有思维链。",
        ),
    }


def validate_node(state: SupervisorState) -> SupervisorState:
    draft = state.get("draft") or {}
    missing, warnings = _adapter.validate(draft)
    activities = _activity(
        state,
        "guard",
        "检查授权与安全边界",
        "验证目标格式和授权声明，并将利用/破坏性请求标记为需要额外确认。",
    )
    if missing:
        activities = _activity(state | {"activities": activities}, "result", "需要补充信息", "缺少：" + "、".join(missing), "blocked")
    else:
        activities = _activity(state | {"activities": activities}, "result", "生成任务草稿", "草稿已准备好，等待你确认后创建任务。")
    return {**state, "missing_fields": missing, "warnings": warnings, "activities": activities, "status": "NEEDS_CLARIFICATION" if missing else "NEEDS_CONFIRMATION"}


def route_after_validate(state: SupervisorState) -> Literal["clarify", "confirm"]:
    return "clarify" if state.get("missing_fields") else "confirm"


def build_graph():
    graph = StateGraph(SupervisorState)
    graph.add_node("parse", parse_node)
    graph.add_node("validate", validate_node)
    graph.add_node("clarify", lambda state: state)
    graph.add_node("confirm", lambda state: state)
    graph.set_entry_point("parse")
    graph.add_edge("parse", "validate")
    graph.add_conditional_edges("validate", route_after_validate, {"clarify": "clarify", "confirm": "confirm"})
    graph.add_edge("clarify", END)
    graph.add_edge("confirm", END)
    return graph.compile()


def run_graph(message: str) -> SupervisorState:
    return build_graph().invoke({"message": message, "activities": []})
