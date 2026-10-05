"""Graph assembly — the incident lifecycle as an explicit LangGraph.

    triage -> investigate -> plan -> policy_gate -> approve? -> remediate
        -> verify -> (report | rollback -> escalate) -> report -> END

Edges are conditional on each node's ``next_node`` signal; the lifecycle
state machine still independently validates every status transition.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sre_agent.graph.nodes.common import wrap_node_errors
from sre_agent.graph.nodes.execution import escalate, remediate, rollback, verify
from sre_agent.graph.nodes.investigate import investigate, triage
from sre_agent.graph.nodes.planning import approve, plan, policy_gate
from sre_agent.graph.nodes.report_node import report
from sre_agent.graph.state import GraphState

_NODES = (
    "triage",
    "investigate",
    "plan",
    "policy_gate",
    "approve",
    "remediate",
    "verify",
    "rollback",
    "escalate",
    "report",
)


def _route(state: GraphState) -> str:
    return state.get("next_node") or "report"


def build_graph(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[GraphState]:
    g: StateGraph[GraphState] = StateGraph(GraphState)
    g.add_node("triage", wrap_node_errors("triage", triage))
    g.add_node("investigate", wrap_node_errors("investigate", investigate))
    g.add_node("plan", wrap_node_errors("plan", plan))
    g.add_node("policy_gate", wrap_node_errors("policy_gate", policy_gate))
    g.add_node("approve", approve)  # interrupt() must propagate — no wrapper
    g.add_node("remediate", wrap_node_errors("remediate", remediate))
    g.add_node("verify", wrap_node_errors("verify", verify))
    g.add_node("rollback", wrap_node_errors("rollback", rollback))
    g.add_node("escalate", escalate)
    g.add_node("report", report)

    g.add_edge(START, "triage")
    g.add_edge("triage", "investigate")
    g.add_conditional_edges("investigate", _route, {"plan": "plan", "escalate": "escalate"})
    g.add_edge("plan", "policy_gate")
    g.add_conditional_edges(
        "policy_gate",
        _route,
        {"approve": "approve", "remediate": "remediate", "escalate": "escalate"},
    )
    g.add_conditional_edges("approve", _route, {"remediate": "remediate", "escalate": "escalate"})
    g.add_edge("remediate", "verify")
    g.add_conditional_edges("verify", _route, {"report": "report", "rollback": "rollback"})
    g.add_edge("rollback", "escalate")
    g.add_edge("escalate", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
