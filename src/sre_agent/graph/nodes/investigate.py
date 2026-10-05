"""Triage + Investigation nodes.

Triage seeds the alert as evidence and enters TRIAGING. Investigation runs a
bounded model<->tool loop with read-only tools, then extracts a structured
InvestigationResult. Evidence collected via tools lands on IncidentState; the
model only sees tool outputs, never state directly.
"""

from __future__ import annotations

import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from sre_agent.graph.nodes.common import (
    bind_state,
    get_ctx,
    incident_briefing,
    move,
)
from sre_agent.graph.prompts import load_prompt
from sre_agent.graph.state import GraphState
from sre_agent.llm.structured import invoke_structured
from sre_agent.models.evidence import EvidenceSourceType, HypothesisStatus, InvestigationResult
from sre_agent.models.incident import IncidentStatus


async def triage(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    move(st, IncidentStatus.TRIAGING, reason="incident intake", actor="node:triage")
    ctx.record_evidence(
        source_type=EvidenceSourceType.ALERT,
        source_name="alert",
        tool_name="triage",
        query_or_parameters={},
        summary=f"alert received: {st.title}",
        structured_data={
            "title": st.title,
            "description": st.description,
            "severity_hint": st.severity.value,
            "affected_services": st.affected_services,
            "constraints": st.constraints,
        },
    )
    ctx.audit_event("triage", incident_id=st.incident_id, severity=st.severity.value)
    return {"incident": st}


async def investigate(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    move(st, IncidentStatus.INVESTIGATING, reason="triage complete", actor="node:investigate")
    backend = ctx.backend
    assert backend is not None, "RunContext.backend not wired"

    from sre_agent.tools.factory import build_readonly_tools

    tools = build_readonly_tools(ctx, backend)
    model = ctx.models["investigator"]
    bound = model.bind_tools(tools)
    tool_node = ToolNode(tools)

    messages: list[BaseMessage] = [
        SystemMessage(content=load_prompt("investigator")),
        HumanMessage(content=incident_briefing(st)),
    ]
    new_msgs: list[BaseMessage] = []

    # Bounded agentic loop. A "turn" = one model call + any tool calls.
    for _ in range(ctx.settings.sre_max_model_turns):
        ctx.tick_model_turn("investigator")
        ai = await bound.ainvoke(messages)
        messages.append(ai)
        new_msgs.append(ai)
        if not ai.tool_calls:
            break
        ev_before = len(st.evidence)
        out = await tool_node.ainvoke({"messages": [ai]})
        tool_msgs: list[BaseMessage] = list(out.get("messages", []))
        messages.extend(tool_msgs)
        new_msgs.extend(tool_msgs)
        if len(st.evidence) == ev_before:
            st.budgets.no_progress_cycles += 1
            if st.budgets.no_progress_cycles >= ctx.settings.sre_max_no_progress_cycles:
                ctx.audit_event("investigation_stalled", turns=st.budgets.model_turns)
                break

    # Structured extraction — the only model output consumed downstream.
    result: InvestigationResult = await invoke_structured(
        model,
        InvestigationResult,
        messages
        + [
            HumanMessage(
                content=(
                    "Investigation complete. Return the InvestigationResult object now: "
                    "summary, hypotheses (with evidence_ids), most_likely_hypothesis_id, "
                    "confidence, insufficient_evidence_reason if applicable."
                )
            )
        ],
    )
    ctx.tick_model_turn("investigator")

    # Reconcile hypotheses into incident state.
    st.hypotheses = list(result.hypotheses)
    for h in st.hypotheses:
        if h.hypothesis_id == result.most_likely_hypothesis_id:
            h.status = HypothesisStatus.CONFIRMED
    ctx.audit_event(
        "investigation_result",
        summary=result.summary[:300],
        confidence=result.confidence,
        hypotheses=len(result.hypotheses),
    )

    insufficient = result.insufficient_evidence_reason
    if insufficient and result.confidence < 0.5:
        st.escalation_reason = f"insufficient evidence: {insufficient}"
        return {"incident": st, "messages": new_msgs, "next_node": "escalate"}
    return {
        "incident": st,
        "messages": new_msgs,
        "investigation_summary": result.summary,
        "next_node": "plan",
    }


def investigate_briefing_for_plan(state: GraphState) -> str:
    """JSON context handed to the planner (investigation + evidence digest)."""
    st = state["incident"]
    digest = [
        {
            "evidence_id": e.evidence_id,
            "source_type": e.source_type.value,
            "source_name": e.source_name,
            "summary": e.summary[:400],
        }
        for e in st.evidence
    ]
    return json.dumps(
        {
            "incident_id": st.incident_id,
            "title": st.title,
            "severity": st.severity.value,
            "affected_services": st.affected_services,
            "investigation_summary": state.get("investigation_summary", ""),
            "hypotheses": [h.model_dump(mode="json") for h in st.hypotheses],
            "evidence": digest,
            "constraints": st.constraints,
        },
        default=str,
    )
