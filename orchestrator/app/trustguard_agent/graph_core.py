from __future__ import annotations

from typing import Any, Literal, TypedDict

try:
    from langgraph.graph import END, StateGraph
except ModuleNotFoundError:  # pragma: no cover - offline smoke fallback.
    END = "__end__"

    class _MiniCompiledGraph:
        def __init__(self, graph: "StateGraph") -> None:
            self.graph = graph

        def invoke(self, state: "AgentState", config: dict[str, Any] | None = None) -> "AgentState":
            node = self.graph.entry_point
            current = dict(state)
            limit = int((config or {}).get("recursion_limit") or 25)
            for _ in range(limit):
                if node == END:
                    return current
                current = self.graph.nodes[node](current)
                if node in self.graph.conditional_edges:
                    router, mapping = self.graph.conditional_edges[node]
                    node = mapping[router(current)]
                else:
                    node = self.graph.edges.get(node, END)
            raise RuntimeError("mini graph recursion limit reached")

    class StateGraph:
        def __init__(self, _state_type: Any) -> None:
            self.nodes: dict[str, Any] = {}
            self.edges: dict[str, str] = {}
            self.conditional_edges: dict[str, Any] = {}
            self.entry_point = ""

        def add_node(self, name: str, fn: Any) -> None:
            self.nodes[name] = fn

        def set_entry_point(self, name: str) -> None:
            self.entry_point = name

        def add_edge(self, src: str, dest: str) -> None:
            self.edges[src] = dest

        def add_conditional_edges(self, src: str, router: Any, mapping: dict[str, str]) -> None:
            self.conditional_edges[src] = (router, mapping)

        def compile(self) -> _MiniCompiledGraph:
            return _MiniCompiledGraph(self)

from .executor_client import execute_tool


class AgentState(TypedDict, total=False):
    task_id: str
    target: str
    allowed_target: str | None
    objective: str
    allowed_tools: list[str]
    max_steps: int
    next_tool: str | None
    completed_tools: list[str]
    findings: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    artifacts: dict[str, Any]
    summary: str
    status: str


DEFAULT_PLAN = ["http_probe", "fingerprint", "risk_review"]


def _append_trace(state: AgentState, event: dict[str, Any]) -> list[dict[str, Any]]:
    return [*state.get("trace", []), event]


def plan_next_tool(state: AgentState) -> AgentState:
    allowed = state.get("allowed_tools") or []
    completed = set(state.get("completed_tools") or [])
    for tool in DEFAULT_PLAN:
        if tool in allowed and tool not in completed:
            return {
                **state,
                "next_tool": tool,
                "trace": _append_trace(
                    state,
                    {
                        "type": "plan.next_tool",
                        "tool": tool,
                        "reason": f"{tool} is the next allowed step in the demo tactical plan",
                    },
                ),
            }
    return {
        **state,
        "next_tool": None,
        "trace": _append_trace(state, {"type": "plan.complete", "reason": "no remaining allowed tools"}),
    }


def route_after_plan(state: AgentState) -> Literal["execute_tool", "summarize"]:
    return "execute_tool" if state.get("next_tool") else "summarize"


def execute_next_tool(state: AgentState) -> AgentState:
    tool = state.get("next_tool")
    if not tool:
        return state
    result = execute_tool(
        task_id=state["task_id"],
        tool_id=tool,
        target=state["target"],
        allowed_target=state.get("allowed_target"),
        params={"objective": state.get("objective", "")},
    )
    findings = [*state.get("findings", []), *result.get("findings", [])]
    artifacts = dict(state.get("artifacts", {}))
    artifacts[tool] = result.get("artifacts", {})
    return {
        **state,
        "completed_tools": [*state.get("completed_tools", []), tool],
        "findings": findings,
        "artifacts": artifacts,
        "trace": _append_trace(
            state,
            {
                "type": "tool.result",
                "tool": tool,
                "status": result.get("status"),
                "summary": result.get("summary"),
                "finding_count": len(result.get("findings") or []),
            },
        ),
        "next_tool": None,
    }


def summarize(state: AgentState) -> AgentState:
    findings = state.get("findings", [])
    high_or_medium = [f for f in findings if str(f.get("severity", "")).lower() in {"high", "medium"}]
    completed = state.get("completed_tools", [])
    if high_or_medium:
        headline = f"Completed {len(completed)} tactical checks and identified {len(high_or_medium)} actionable risk(s)."
    else:
        headline = f"Completed {len(completed)} tactical checks with no high-confidence actionable risk."
    return {
        **state,
        "status": "OK",
        "summary": headline,
        "trace": _append_trace(
            state,
            {
                "type": "agent.summary",
                "completed_tools": completed,
                "finding_count": len(findings),
                "actionable_count": len(high_or_medium),
            },
        ),
    }


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan_next_tool", plan_next_tool)
    graph.add_node("execute_tool", execute_next_tool)
    graph.add_node("summarize", summarize)
    graph.set_entry_point("plan_next_tool")
    graph.add_conditional_edges(
        "plan_next_tool",
        route_after_plan,
        {"execute_tool": "execute_tool", "summarize": "summarize"},
    )
    graph.add_edge("execute_tool", "plan_next_tool")
    graph.add_edge("summarize", END)
    return graph.compile()


def run_agent_dict(req: dict[str, Any]) -> dict[str, Any]:
    initial: AgentState = {
        "task_id": str(req.get("task_id") or "demo-task"),
        "target": str(req["target"]),
        "allowed_target": req.get("allowed_target") or req["target"],
        "objective": str(req.get("objective") or "Assess the target and produce concise security findings."),
        "allowed_tools": list(req.get("allowed_tools") or DEFAULT_PLAN),
        "max_steps": int(req.get("max_steps") or 6),
        "completed_tools": [],
        "findings": [],
        "trace": [
            {
                "type": "agent.start",
                "target": req["target"],
                "objective": req.get("objective") or "Assess the target and produce concise security findings.",
                "allowed_tools": list(req.get("allowed_tools") or DEFAULT_PLAN),
            }
        ],
        "artifacts": {},
        "status": "RUNNING",
    }
    final = build_graph().invoke(initial, {"recursion_limit": max(8, int(initial["max_steps"]) * 3)})
    return {
        "status": str(final.get("status") or "FAILED"),
        "task_id": str(initial["task_id"]),
        "target": str(initial["target"]),
        "summary": str(final.get("summary") or ""),
        "findings": list(final.get("findings") or []),
        "trace": list(final.get("trace") or []),
        "artifacts": dict(final.get("artifacts") or {}),
    }

