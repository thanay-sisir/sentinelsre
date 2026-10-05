"""Report node — deterministic assembly + model-written narrative.

Structured fields (timeline, evidence refs, actions, verification) are built
from state directly. The reporter model only fills narrative fields via a
small ReportNarrative schema, so it cannot invent evidence ids or actions.
In scripted mode (no reporter model) canned narrative text is used.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from sre_agent.graph.nodes.common import bind_state, get_ctx
from sre_agent.graph.prompts import PROMPT_VERSIONS, load_prompt
from sre_agent.graph.state import GraphState
from sre_agent.llm.structured import invoke_structured
from sre_agent.models.incident import IncidentState
from sre_agent.models.report import IncidentReport, TimelineEntry
from sre_agent.reporting.renderer import write_report


class ReportNarrative(BaseModel):
    """Only the free-text fields the reporter model may write."""

    customer_impact: str = ""
    symptoms: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list, max_length=4)
    remaining_risks: list[str] = Field(default_factory=list, max_length=3)


def assemble_report(st: IncidentState, prompt_version: str) -> IncidentReport:
    """Build the report's structured fields deterministically from state."""
    timeline: list[TimelineEntry] = [
        TimelineEntry(
            timestamp=t.timestamp,
            actor=t.actor,
            event=f"{t.previous_status.value} -> {t.new_status.value}",
            detail=t.reason,
        )
        for t in st.transitions
    ]
    last_plan = st.pending_plan or (st.proposed_actions[-1] if st.proposed_actions else None)
    started = st.started_at
    ended = st.updated_at
    return IncidentReport(
        incident_id=st.incident_id,
        title=st.title,
        final_status=st.status.value,
        severity=st.severity.value,
        started_at=started,
        resolved_at=ended,
        duration_seconds=(ended - started).total_seconds(),
        affected_services=st.affected_services,
        root_cause=last_plan.root_cause if last_plan else "undetermined",
        root_cause_confidence=last_plan.root_cause_confidence if last_plan else 0.0,
        supporting_evidence=[
            e for e in (last_plan.supporting_evidence_ids if last_plan else []) if e in st.evidence_ids()
        ],
        timeline=timeline,
        actions_taken=[
            f"{a.action_id[:8]} {a.tool_name} on {a.target}: "
            f"{'ok' if a.success else 'FAILED'} {a.result_summary[:120]}"
            for a in st.executed_actions
        ],
        verification_results=[
            {
                "check_type": v.check_type.value,
                "target": v.target,
                "passed": v.passed,
                "observed": v.observed,
            }
            for v in st.verification_results
        ],
        rollback_performed=any(e.kind.value == "ROLLBACK_EXECUTED" for e in st.safety_events),
        safety_events=[f"{e.kind.value}: {e.detail[:160]}" for e in st.safety_events],
        escalation_reason=st.escalation_reason,
        agent_version="0.1.0",
        agent_framework="langgraph",
        prompt_version=prompt_version,
        model_configuration={},
        langsmith_trace_id=st.trace_metadata.get("langsmith_trace_id"),
    )


async def report(state: GraphState, config: RunnableConfig) -> GraphState:
    ctx = get_ctx(config)
    st = bind_state(ctx, state)
    prompt_version = "+".join(PROMPT_VERSIONS.values())
    rep = assemble_report(st, prompt_version)

    model = ctx.models.get("reporter")
    if model is not None:
        ctx.tick_model_turn("reporter")
        narrative: ReportNarrative = await invoke_structured(
            model,
            ReportNarrative,
            [
                SystemMessage(content=load_prompt("reporter")),
                HumanMessage(
                    content=(
                        "Incident state (authoritative):\n"
                        + st.model_dump_json(exclude={"transitions"})[:24000]
                    )
                ),
            ],
        )
    else:
        narrative = _canned_narrative(st)

    rep.customer_impact = narrative.customer_impact
    rep.symptoms = narrative.symptoms
    rep.recommendations = narrative.recommendations
    rep.remaining_risks = narrative.remaining_risks
    rep.model_configuration = {
        "provider": ctx.settings.sre_llm_provider,
        "models": {role: ctx.settings.model_for(role) for role in PROMPT_VERSIONS},
        "mode": "scripted" if ctx.models.get("reporter") is None else "live",
    }

    json_path, md_path = write_report(rep, st, ctx.settings.sre_artifact_dir)
    st.final_report_path = str(md_path)
    ctx.audit_event("report_written", json=str(json_path), markdown=str(md_path), status=st.status.value)
    return {"incident": st}


def _canned_narrative(st: IncidentState) -> ReportNarrative:
    plan = st.pending_plan or (st.proposed_actions[-1] if st.proposed_actions else None)
    symptom = f"{st.title} — services: {', '.join(st.affected_services) or 'unknown'}"
    return ReportNarrative(
        customer_impact=(
            f"Checkout functionality was degraded ({st.title})."
            if "checkout" in st.title.lower() or "checkout" in str(st.affected_services)
            else f"Service degradation: {st.title}."
        ),
        symptoms=[symptom, f"final status {st.status.value}"],
        recommendations=[
            "Pin dependency endpoints via config review CI check",
            "Add dependency-mismatch alert (configured vs expected URL)",
        ]
        if plan and plan.operation == "patch_runtime_config"
        else ["Increase observability coverage for this failure class"],
        remaining_risks=[] if st.status.value == "RESOLVED" else ["manual follow-up required"],
    )
