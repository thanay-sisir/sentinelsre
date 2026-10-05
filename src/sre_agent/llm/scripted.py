"""Scripted (keyless) incident driver — the deterministic offline mode.

Runs the REAL tool layer, policy engine, token redemption, verification
executor, and report writer — identical artifacts to a live LLM run. Only
the model-authored steps are replaced: investigation is a fixed diagnostic
sequence, the plan is derived from dependency-mismatch evidence, and human
approval is recorded as `scripted:harness`.

Today the scripted path knows one fault class: a dependency endpoint
misconfiguration (the wrong-inventory-endpoint scenario). Anything else
escalates honestly as insufficient evidence rather than guessing.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from sre_agent.graph.nodes.common import (
    move,
    plan_ops,
    record_approval_request,
)
from sre_agent.graph.nodes.execution import escalate, remediate, rollback, verify
from sre_agent.graph.nodes.investigate import triage
from sre_agent.graph.nodes.planning import policy_gate
from sre_agent.graph.nodes.report_node import report
from sre_agent.graph.state import GraphState
from sre_agent.models.evidence import (
    EvidenceSourceType,
    Hypothesis,
    HypothesisStatus,
)
from sre_agent.models.incident import IncidentState, IncidentStatus
from sre_agent.models.policy import ApprovalStatus
from sre_agent.models.remediation import CheckType, RemediationPlan, VerificationStep
from sre_agent.tools.factory import build_readonly_tools


def _config_for(ctx: Any) -> RunnableConfig:
    return {"configurable": {"ctx": ctx}}


async def run_scripted_incident(ctx: Any) -> IncidentState:
    """Drive one scripted incident end-to-end. ctx.state must be RECEIVED.

    Node functions return *partial* updates like the real graph — merge each
    into gstate (the checkpointer would do this in a live run).
    """
    cfg = _config_for(ctx)
    st = ctx.state
    gstate: GraphState = {"incident": st}

    gstate.update(await triage(gstate, cfg))
    move(
        st,
        IncidentStatus.INVESTIGATING,
        reason="scripted investigation sequence",
        actor="scripted:investigate",
    )
    analysis = await _scripted_investigate(ctx, st)

    move(
        st,
        IncidentStatus.PLANNING_REMEDIATION,
        reason="scripted analysis produced a plan",
        actor="scripted:plan",
    )
    if analysis is None:
        st.escalation_reason = (
            "scripted analyzer found no actionable dependency misconfiguration "
            "(scripted mode only handles this fault class)"
        )
        gstate.update(await escalate(gstate, cfg))
        gstate.update(await report(gstate, cfg))
        return gstate["incident"]

    plan = _build_plan(ctx, st, analysis)
    st.proposed_actions.append(plan)
    st.pending_plan = plan
    gstate["plan"] = plan
    ctx.audit_event(
        "plan_proposed",
        plan_id=plan.plan_id,
        operation=plan.operation,
        scripted=True,
    )

    gstate.update(await policy_gate(gstate, cfg))
    decision = gstate.get("policy_decision")
    if gstate.get("next_node") == "escalate" or (decision and not decision.allowed):
        gstate.update(await escalate(gstate, cfg))
        gstate.update(await report(gstate, cfg))
        return gstate["incident"]

    if decision and decision.requires_approval:
        move(
            st,
            IncidentStatus.AWAITING_APPROVAL,
            reason="policy requires approval — scripted harness approves",
            actor="scripted:approve",
        )
        for op, params in plan_ops(plan):
            record_approval_request(
                st,
                plan,
                action_id=f"{plan.plan_id}:{op}",
                operation=op,
                target=plan.target_service,
                params=params,
                status=ApprovalStatus.APPROVED,
                approver="scripted:harness",
                reason="deterministic scripted approval",
            )
        ctx.audit_event("approval_decision", approved=True, approver="scripted:harness")
        gstate["approval_decision"] = {
            "approved": True,
            "approver": "scripted:harness",
            "reason": "deterministic scripted approval",
        }

    gstate.update(await remediate(gstate, cfg))  # commander absent -> deterministic fallback
    gstate.update(await verify(gstate, cfg))
    if gstate.get("next_node") == "rollback":
        gstate.update(await rollback(gstate, cfg))
        gstate.update(await escalate(gstate, cfg))
    gstate.update(await report(gstate, cfg))
    return gstate["incident"]


async def _scripted_investigate(ctx: Any, st: IncidentState) -> dict[str, Any] | None:
    """Fixed diagnostic sequence through the real read-only tools.

    Returns analysis dict {dep, field, configured_url, expected_url,
    config_hash, evidence_ids} when a dependency mismatch is found.
    """
    assert ctx.backend is not None
    tools = {t.name: t for t in build_readonly_tools(ctx, ctx.backend)}
    affected = st.affected_services or [s.name for s in await ctx.backend.list_services()]

    await tools["list_services"].ainvoke({})
    for svc in affected:
        await tools["get_service_status"].ainvoke({"service_name": svc})
    await tools["run_synthetic_transaction"].ainvoke({"transaction_name": "checkout"})

    analysis: dict[str, Any] | None = None
    dep_evidence_ids: list[str] = []
    for svc in affected:
        await tools["get_dependency_status"].ainvoke({"service_name": svc})
        dep_rec = next(
            (
                e
                for e in reversed(st.evidence)
                if e.source_type == EvidenceSourceType.DEPENDENCY and e.source_name == svc
            ),
            None,
        )
        if not dep_rec:
            continue
        dep_evidence_ids.append(dep_rec.evidence_id)
        for dep in dep_rec.structured_data.get("dependencies", []):
            if dep.get("urls_match") is False:
                field = f"{dep['name'].split('-')[0]}_url"
                cfg_out = await tools["read_runtime_config"].ainvoke({"service_name": svc})
                import json

                cfg_data = json.loads(cfg_out)
                await tools["get_recent_logs"].ainvoke({"service_name": svc, "limit": 30})
                analysis = {
                    "dep": dep,
                    "service": svc,
                    "field": field,
                    "configured_url": dep["configured_url"],
                    "expected_url": dep["expected_url"],
                    "config_hash": cfg_data.get("content_hash", ""),
                    "dep_evidence_id": dep_rec.evidence_id,
                }

    if analysis is None:
        return None

    # evidence ids: dependency mismatch + config + failing synthetic + health
    supporting = [analysis["dep_evidence_id"]]
    for e in st.evidence:
        if e.source_type in (
            EvidenceSourceType.SYNTHETIC_CHECK,
            EvidenceSourceType.CONFIGURATION,
            EvidenceSourceType.LOG,
        ):
            supporting.append(e.evidence_id)
    analysis["supporting_evidence_ids"] = supporting[-6:]

    h = Hypothesis(
        description=(
            f"{analysis['service']} is configured to reach "
            f"{analysis['dep']['name']} at {analysis['configured_url']}, but the "
            f"expected endpoint is {analysis['expected_url']}. Readiness and "
            f"synthetic checkout fail while the service process stays up."
        ),
        candidate_root_cause=(f"runtime config {analysis['field']} points at a wrong endpoint"),
        supporting_evidence_ids=analysis["supporting_evidence_ids"],
        confidence=0.92,
        status=HypothesisStatus.CONFIRMED,
        reasoning_summary=(
            "dependency status shows urls_match=false; synthetic checkout "
            "fails with 503; process remains RUNNING — config drift, not crash"
        ),
    )
    st.hypotheses = [h]
    ctx.audit_event(
        "investigation_result",
        scripted=True,
        confidence=h.confidence,
        root_cause=h.candidate_root_cause,
    )
    return analysis


def _build_plan(ctx: Any, st: IncidentState, analysis: dict[str, Any]) -> RemediationPlan:
    return RemediationPlan(
        incident_id=st.incident_id,
        root_cause=(
            f"checkout-service config field {analysis['field']} = "
            f"{analysis['configured_url']} (expected {analysis['expected_url']})"
        ),
        root_cause_confidence=0.92,
        supporting_evidence_ids=analysis["supporting_evidence_ids"],
        target_service=analysis["service"],
        operation="patch_runtime_config",
        normalized_parameters={
            "changes": {analysis["field"]: analysis["expected_url"]},
            "expected_config_hash": analysis["config_hash"],
        },
        post_operation="reload_service",
        post_parameters={},
        expected_result=(f"{analysis['service']} readiness recovers; synthetic checkout returns HTTP 200"),
        verification_steps=[
            VerificationStep(
                check_type=CheckType.PROCESS_RUNNING,
                target=analysis["service"],
                expected="service process running",
            ),
            VerificationStep(
                check_type=CheckType.READINESS,
                target=analysis["service"],
                expected="readiness 200",
            ),
            VerificationStep(
                check_type=CheckType.CONFIG_VALUE,
                target=analysis["service"],
                expected="config shows expected endpoint",
                params={"key": analysis["field"], "value": analysis["expected_url"]},
            ),
            VerificationStep(
                check_type=CheckType.SYNTHETIC,
                target="checkout",
                expected="synthetic checkout succeeds",
            ),
        ],
        rollback_operation="restore_runtime_config",
        rollback_parameters={"backup_id": "AUTO"},
        justification=(
            "Smallest reversible fix: restore the single drifted config field "
            "with optimistic-concurrency hash check, then reload. Rollback is "
            "the auto-created config backup."
        ),
    )
