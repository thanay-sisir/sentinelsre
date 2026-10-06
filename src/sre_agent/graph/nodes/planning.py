"""Planning + policy-gate + approval nodes.

The planner emits a RemediationPlan as structured output. policy_gate runs
the deterministic engine. approve pauses the graph with interrupt() when the
policy requires a human — the `incident approve` CLI command resumes it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from sre_agent.graph.nodes.common import (
    bind_state,
    get_ctx,
    move,
    plan_ops,
    record_approval_request,
)
from sre_agent.graph.nodes.investigate import investigate_briefing_for_plan
from sre_agent.graph.prompts import load_prompt
from sre_agent.graph.state import GraphState
from sre_agent.llm.structured import invoke_structured
from sre_agent.models.incident import IncidentStatus
from sre_agent.models.policy import ApprovalStatus, SafetyEvent, SafetyEventKind
from sre_agent.models.remediation import RemediationPlan


async def plan(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    if st.status != IncidentStatus.PLANNING_REMEDIATION:
        move(
            st,
            IncidentStatus.PLANNING_REMEDIATION,
            reason="investigation produced hypotheses",
            actor="node:plan",
        )
    ctx.tick_model_turn("planner")
    briefing = investigate_briefing_for_plan(state)
    if feedback := state.get("plan_feedback"):
        briefing += f"\n\nPOLICY FEEDBACK ON PRIOR PLAN:\n{feedback}"
    plan_obj: RemediationPlan = await invoke_structured(
        ctx.models["planner"],
        RemediationPlan,
        [
            SystemMessage(content=load_prompt("planner")),
            HumanMessage(content=briefing),
        ],
    )
    plan_obj.incident_id = st.incident_id
    st.proposed_actions.append(plan_obj)
    st.pending_plan = plan_obj
    ctx.audit_event(
        "plan_proposed",
        plan_id=plan_obj.plan_id,
        operation=plan_obj.operation,
        target=plan_obj.target_service,
        confidence=plan_obj.root_cause_confidence,
    )
    return {"incident": st, "plan": plan_obj, "next_node": "policy_gate"}


async def policy_gate(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    plan_obj = state["plan"]
    assert plan_obj is not None, "policy_gate reached without a plan"
    decision = ctx.policy.evaluate(plan_obj, st)
    ctx.audit_event(
        "policy_decision",
        plan_id=plan_obj.plan_id,
        allowed=decision.allowed,
        risk=decision.risk_level.value,
        requires_approval=decision.requires_approval,
        reason=decision.reason,
        rules=decision.policy_rule_ids,
    )
    if not decision.allowed:
        st.add_safety_event(
            SafetyEvent(
                event_id=f"se-{plan_obj.plan_id[:12]}",
                incident_id=st.incident_id,
                kind=SafetyEventKind.POLICY_DENIAL,
                detail=decision.reason,
                timestamp=datetime.now(UTC),
            )
        )
        # One bounded re-plan when the ONLY failure is evidence grounding —
        # the planner gets the denial reason fed back so it can cite real
        # evidence ids. Any other denial (forbidden op, confidence, mode)
        # escalates immediately.
        replans = ctx.counters.get("replans", 0)
        if decision.reason.startswith("insufficient grounded evidence") and replans < 1:
            ctx.counters["replans"] = replans + 1
            ctx.audit_event("replan_requested", plan_id=plan_obj.plan_id, reason=decision.reason)
            return {
                "incident": st,
                "policy_decision": decision,
                "plan_feedback": (
                    f"Your previous plan was denied by the policy engine: {decision.reason}. "
                    "Re-plan and cite only evidence_id values that appear verbatim "
                    "in the evidence digest above."
                ),
                "next_node": "plan",
            }
        st.escalation_reason = f"policy denied {plan_obj.operation}: {decision.reason}"
        return {"incident": st, "policy_decision": decision, "next_node": "escalate"}
    nxt = "approve" if decision.requires_approval else "remediate"
    return {"incident": st, "policy_decision": decision, "next_node": nxt}


async def approve(state: GraphState, config: RunnableConfig) -> GraphState:
    """Pause for human approval via interrupt(); resumed by CLI `approve`."""
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    plan_obj = state["plan"]
    decision = state["policy_decision"]
    assert plan_obj is not None and decision is not None
    move(
        st,
        IncidentStatus.AWAITING_APPROVAL,
        reason="policy requires human approval",
        actor="node:approve",
    )
    for op, params in plan_ops(plan_obj):
        record_approval_request(
            st,
            plan_obj,
            action_id=f"{plan_obj.plan_id}:{op}",
            operation=op,
            target=plan_obj.target_service,
            params=params,
            status=ApprovalStatus.PENDING,
        )
    ctx.audit_event(
        "approval_requested",
        plan_id=plan_obj.plan_id,
        ops=[op for op, _ in plan_ops(plan_obj)],
    )

    payload = {
        "type": "approval_required",
        "incident_id": st.incident_id,
        "plan_id": plan_obj.plan_id,
        "root_cause": plan_obj.root_cause,
        "operations": [
            {"operation": op, "target": plan_obj.target_service, "parameters": p}
            for op, p in plan_ops(plan_obj)
        ],
        "risk_level": decision.risk_level.value,
        "policy_reason": decision.reason,
        "rollback": {
            "operation": plan_obj.rollback_operation,
            "parameters": plan_obj.rollback_parameters,
        },
    }
    resume = interrupt(payload)  # --- graph pauses here; CLI resumes ---
    # resume: {"approved": bool, "approver": str, "reason": str}
    approved = bool(resume.get("approved"))
    approver = resume.get("approver", "human:unknown")
    reason = resume.get("reason")
    for req in st.approval_requests:
        if req.plan_id == plan_obj.plan_id and req.status == ApprovalStatus.PENDING:
            req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
            req.decided_at = datetime.now(UTC)
            req.approver = approver
            req.decision_reason = reason
    ctx.audit_event(
        "approval_decision",
        plan_id=plan_obj.plan_id,
        approved=approved,
        approver=approver,
        reason=reason,
    )
    if not approved:
        st.escalation_reason = f"human denied approval: {reason or 'no reason given'}"
        return {"incident": st, "approval_decision": resume, "next_node": "escalate"}
    return {"incident": st, "approval_decision": resume, "next_node": "remediate"}
