"""Remediation, verification, rollback, escalation nodes.

Remediate issues fresh single-use tokens (scoped to each approved step) and
binds ONLY the approved mutating tools to the commander model — the model
cannot even see an unapproved tool. If the commander fails to execute an
approved step, deterministic fallback performs it through the same
token-gated path so audit is identical.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from sre_agent.graph.nodes.common import (
    _op_kwargs,
    bind_state,
    get_ctx,
    move,
    plan_ops,
    record_approval_request,
    resolve_params,
)
from sre_agent.graph.prompts import load_prompt
from sre_agent.graph.state import GraphState
from sre_agent.models.evidence import EvidenceSourceType
from sre_agent.models.incident import IncidentState, IncidentStatus
from sre_agent.models.policy import ApprovalStatus, SafetyEvent, SafetyEventKind
from sre_agent.models.remediation import RemediationPlan
from sre_agent.tools.factory import RunContext
from sre_agent.tools.verification import run_verification_steps


def _issue_tokens(ctx: RunContext, st: IncidentState, plan_obj: RemediationPlan, issued_by: str) -> None:
    """Mint a fresh single-use token per approved step."""
    assert ctx.approvals is not None
    for op, params in plan_ops(plan_obj):
        resolved = resolve_params(plan_obj, params, ctx)
        token = ctx.approvals.issue(
            incident_id=st.incident_id,
            action_id=f"{plan_obj.plan_id}:{op}",
            operation=op,
            target=plan_obj.target_service,
            params=resolved,
            issued_by=issued_by,
        )
        ctx.bound_tokens[op] = token
        ctx.audit_event("token_issued", operation=op, target=plan_obj.target_service, by=issued_by)


def _approved_ops(plan_obj: RemediationPlan) -> set[str]:
    return {op for op, _ in plan_ops(plan_obj)}


async def remediate(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    plan_obj = state["plan"]
    assert plan_obj is not None, "remediate reached without a plan"
    decision = state.get("policy_decision")
    approval = state.get("approval_decision") or {}
    backend = ctx.backend
    assert backend is not None

    human_approved = bool(decision and decision.requires_approval)
    issued_by = f"human:{approval.get('approver', 'cli')}" if human_approved else "auto-policy:auto_safe"
    if not human_approved:
        # Keep the approval trail symmetric: policy auto-approval is recorded
        # as an APPROVED request so reports/audits show who authorized what.
        for op, params in plan_ops(plan_obj):
            record_approval_request(
                st,
                plan_obj,
                action_id=f"{plan_obj.plan_id}:{op}",
                operation=op,
                target=plan_obj.target_service,
                params=params,
                status=ApprovalStatus.APPROVED,
                approver=issued_by,
                reason="auto-approved by deterministic policy (auto_safe mode)",
            )
    _issue_tokens(ctx, st, plan_obj, issued_by)
    move(
        st,
        IncidentStatus.REMEDIATING,
        reason=f"tokens issued by {issued_by}",
        actor="node:remediate",
    )
    st.budgets.remediation_attempts += 1

    from sre_agent.tools.factory import build_mutating_tools

    tools = build_mutating_tools(ctx, backend)
    approved = _approved_ops(plan_obj)
    tools_by_name = {t.name: t for t in tools if t.name in approved}

    model = ctx.models.get("commander")
    if model is not None:
        await _commander_loop(ctx, st, plan_obj, model, tools_by_name)

    # Fallback: any approved step without a successful ActionRecord runs
    # deterministically through the same token-gated tool path.
    executed_ok = {a.tool_name for a in st.executed_actions if a.plan_id == plan_obj.plan_id and a.success}
    missing = {op for op, _ in plan_ops(plan_obj)} - executed_ok
    if missing:
        ctx.audit_event("commander_fallback", missing_ops=sorted(missing))
        for op, params in plan_ops(plan_obj):
            if op in missing:
                tool = tools_by_name[op]
                await tool.ainvoke(
                    _op_kwargs(op, plan_obj.target_service, resolve_params(plan_obj, params, ctx))
                )
        ctx.audit_event("commander_fallback_done", ops=sorted(missing))

    move(
        st,
        IncidentStatus.VERIFYING,
        reason="approved operations executed",
        actor="node:remediate",
    )
    return {"incident": st, "next_node": "verify"}


async def _commander_loop(
    ctx: RunContext,
    st: IncidentState,
    plan_obj: RemediationPlan,
    model: Any,
    tools_by_name: dict[str, Any],
) -> None:
    """Bounded loop letting the commander model invoke the approved tools."""
    tools = list(tools_by_name.values())
    bound = model.bind_tools(tools)
    tool_node = ToolNode(tools)
    ops_desc = "\n".join(
        f"- {op} on {plan_obj.target_service} with params "
        f"{json.dumps(resolve_params(plan_obj, p, ctx), default=str)}"
        for op, p in plan_ops(plan_obj)
    )
    messages: list[BaseMessage] = [
        SystemMessage(content=load_prompt("commander")),
        HumanMessage(
            content=(
                f"Approved plan {plan_obj.plan_id}. Execute exactly these steps "
                f"in order:\n{ops_desc}\n"
                f"Expected result: {plan_obj.expected_result}"
            )
        ),
    ]
    for _ in range(6):  # commander turns are small and fixed
        ctx.tick_model_turn("commander")
        ai = await bound.ainvoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        out = await tool_node.ainvoke({"messages": [ai]})
        messages.extend(out.get("messages", []))
        done = {a.tool_name for a in st.executed_actions if a.plan_id == plan_obj.plan_id}
        if _approved_ops(plan_obj) <= done:
            break


async def verify(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    plan_obj = state["plan"]
    assert plan_obj is not None
    backend = ctx.backend
    assert backend is not None

    results = await run_verification_steps(
        backend,
        plan_obj.verification_steps,
        retries=ctx.settings.sre_max_verification_retries,
    )
    st.verification_results.extend(results)
    st.budgets.verification_retries += 1
    for res in results:
        ctx.record_evidence(
            source_type=(
                EvidenceSourceType.SYNTHETIC_CHECK
                if res.check_type.value == "synthetic"
                else EvidenceSourceType.HEALTH_CHECK
            ),
            source_name=res.target,
            tool_name="verify_node",
            query_or_parameters={"check_type": res.check_type.value},
            summary=(
                f"{res.check_type.value} {res.target}: {'PASS' if res.passed else 'FAIL'} — {res.observed}"
            ),
            structured_data=res.details,
        )
        ctx.audit_event(
            "verification_check",
            check=res.check_type.value,
            target=res.target,
            passed=res.passed,
            observed=res.observed[:200],
        )

    if all(r.passed for r in results):
        move(
            st,
            IncidentStatus.RESOLVED,
            reason="all verification checks passed",
            actor="node:verify",
        )
        return {"incident": st, "next_node": "report"}

    st.add_safety_event(
        SafetyEvent(
            event_id=f"se-vf-{int(datetime.now(UTC).timestamp() * 1000)}",
            incident_id=st.incident_id,
            kind=SafetyEventKind.VERIFICATION_FAILED,
            detail="; ".join(r.observed for r in results if not r.passed)[:500],
            timestamp=datetime.now(UTC),
        )
    )
    move(
        st,
        IncidentStatus.ROLLING_BACK,
        reason="verification failed — restoring prior state",
        actor="node:verify",
    )
    return {"incident": st, "next_node": "rollback"}


async def rollback(state: GraphState, config: RunnableConfig) -> GraphState:
    """Deterministic rollback — never delegated to the model.

    Restores the config backup (AUTO -> ctx.last_backup_id), reloads the
    service, then health-checks it. All through the same token path.
    """
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    plan_obj = state["plan"]
    assert plan_obj is not None
    backend = ctx.backend
    assert backend is not None

    if plan_obj.rollback_operation:
        assert ctx.approvals is not None
        params = resolve_params(plan_obj, dict(plan_obj.rollback_parameters), ctx)
        if params.get("backup_id") in (None, "", "AUTO") and ctx.last_backup_id:
            params["backup_id"] = ctx.last_backup_id
        token = ctx.approvals.issue(
            incident_id=st.incident_id,
            action_id=f"{plan_obj.plan_id}:rollback",
            operation=plan_obj.rollback_operation,
            target=plan_obj.target_service,
            params=params,
            issued_by="auto-policy:rollback",
        )
        ctx.bound_tokens[plan_obj.rollback_operation] = token

        from sre_agent.tools.factory import build_mutating_tools

        tools_by_name = {t.name: t for t in build_mutating_tools(ctx, backend)}
        tool = tools_by_name[plan_obj.rollback_operation]
        await tool.ainvoke(_op_kwargs(plan_obj.rollback_operation, plan_obj.target_service, params))
        # mark the restored action as a rollback of the original
        orig = next(
            (a for a in st.executed_actions if a.plan_id == plan_obj.plan_id and a.success),
            None,
        )
        last = st.executed_actions[-1] if st.executed_actions else None
        if last and orig:
            last.rollback_reference = orig.action_id
        # config restores need a reload to take effect
        if plan_obj.rollback_operation == "restore_runtime_config":
            rl = ctx.approvals.issue(
                incident_id=st.incident_id,
                action_id=f"{plan_obj.plan_id}:rollback-reload",
                operation="reload_service",
                target=plan_obj.target_service,
                params={},
                issued_by="auto-policy:rollback",
            )
            ctx.bound_tokens["reload_service"] = rl
            await tools_by_name["reload_service"].ainvoke({"service_name": plan_obj.target_service})
        # observe post-rollback health
        hc = await backend.health_check(plan_obj.target_service)
        ctx.record_evidence(
            source_type=EvidenceSourceType.HEALTH_CHECK,
            source_name=plan_obj.target_service,
            tool_name="rollback_node",
            query_or_parameters={},
            summary=f"post-rollback health: alive={hc.alive} ready={hc.ready}",
            structured_data=hc.model_dump(mode="json"),
        )
    else:
        ctx.audit_event("rollback_skipped", reason="plan had no rollback_operation")

    st.add_safety_event(
        SafetyEvent(
            event_id=f"se-rb-{int(datetime.now(UTC).timestamp() * 1000)}",
            incident_id=st.incident_id,
            kind=SafetyEventKind.ROLLBACK_EXECUTED,
            detail=f"rolled back {plan_obj.operation} on {plan_obj.target_service}",
            timestamp=datetime.now(UTC),
        )
    )
    st.escalation_reason = (
        st.escalation_reason or f"verification failed after {plan_obj.operation}; rolled back prior state"
    )
    move(
        st,
        IncidentStatus.ROLLED_BACK,
        reason="prior state restored",
        actor="node:rollback",
    )
    return {"incident": st, "next_node": "escalate"}


async def escalate(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    if st.status != IncidentStatus.ESCALATED:
        move(
            st,
            IncidentStatus.ESCALATED,
            reason=st.escalation_reason or "escalated",
            actor="node:escalate",
        )
    ctx.audit_event("escalated", reason=st.escalation_reason)
    return {"incident": st, "next_node": "report"}
