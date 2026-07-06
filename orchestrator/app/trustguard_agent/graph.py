from __future__ import annotations

from .graph_core import build_graph, run_agent_dict
from .models import AgentRunRequest, AgentRunResponse

__all__ = ["build_graph", "run_agent", "run_agent_dict"]


def run_agent(req: AgentRunRequest) -> AgentRunResponse:
    return AgentRunResponse.model_validate(run_agent_dict(req.model_dump()))

