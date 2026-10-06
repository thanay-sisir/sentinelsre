"""LangGraph state for the incident workflow.

The authoritative incident record is a single Pydantic ``IncidentState``
mutated by nodes and re-bound to ``RunContext.state`` at every node entry —
the checkpointer may round-trip it through serde, so node code must not rely
on identity across nodes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import add_messages

from sre_agent.models.incident import IncidentState
from sre_agent.models.policy import PolicyDecision
from sre_agent.models.remediation import RemediationPlan


class GraphState(TypedDict, total=False):
    incident: IncidentState
    # Conversation scratchpad for the investigation loop.
    messages: Annotated[list[BaseMessage], add_messages]
    investigation_summary: str
    plan: RemediationPlan | None
    policy_decision: PolicyDecision | None
    approval_decision: dict[str, Any] | None  # human resume payload
    plan_feedback: str  # policy-denial reason fed back into a re-plan
    next_node: str  # routing signal set by nodes
    error: str | None


# A node returns a partial state update — GraphState is total=False.
NodeFn = Callable[[GraphState, RunnableConfig], Awaitable[GraphState]]
