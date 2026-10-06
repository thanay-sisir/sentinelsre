"""Shared node helpers: ctx extraction, transitions, plan execution."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from sre_agent import lifecycle
from sre_agent.backends.base import OpsBackend
from sre_agent.exceptions import (
    ApprovalError,
    BackendOperationError,
    BudgetExceededError,
    PolicyDeniedError,
    StructuredOutputError,
)
from sre_agent.graph.state import GraphState
from sre_agent.models.incident import IncidentState, IncidentStatus, StateTransition
from sre_agent.models.policy import ApprovalRequest, ApprovalStatus
from sre_agent.models.remediation import RemediationPlan
from sre_agent.tools.factory import RunContext


def get_ctx(config: RunnableConfig) -> RunContext:
    ctx = config["configurable"]["ctx"]
    assert isinstance(ctx, RunContext)
    return ctx


def bind_state(ctx: RunContext, state: GraphState) -> IncidentState:
    """Re-bind ctx.state to the incident object from this node's input."""
    incident = state["incident"]
    assert isinstance(incident, IncidentState)
    ctx.state = incident
    return incident


def move(
    state: IncidentState,
    to: IncidentStatus,
    *,
    reason: str,
    actor: str,
) -> StateTransition:
    rec = lifecycle.transition(state, to, reason=reason, actor=actor)
    return rec


def plan_ops(plan: RemediationPlan) -> list[tuple[str, dict[str, Any]]]:
    """The approved operation sequence: primary + optional post-step."""
    ops = [(plan.operation, dict(plan.normalized_parameters))]
    if plan.post_operation:
        ops.append((plan.post_operation, dict(plan.post_parameters)))
    return ops


_HEX = re.compile(r"^[0-9a-fA-F]{8,64}$")


def resolve_params(plan: RemediationPlan, params: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """Fill runtime placeholders and sanitize model-supplied values.

    'AUTO' backup ids resolve from ctx.last_backup_id. An
    expected_config_hash that isn't a plausible sha-hex string (models
    sometimes emit literal placeholders like 'current-hash') degrades to
    "" — meaning skip optimistic concurrency — rather than hard-failing an
    approved plan. The config store still takes a backup before applying.
    """
    resolved = dict(params)
    for key, val in resolved.items():
        if key == "expected_config_hash" and isinstance(val, str) and val and not _HEX.match(val):
            resolved[key] = ""
        elif val == "AUTO":
            if key == "backup_id" and ctx.last_backup_id:
                resolved[key] = ctx.last_backup_id
            else:
                resolved[key] = val
    return resolved


async def execute_plan_deterministic(
    ctx: RunContext,
    backend: OpsBackend,
    plan: RemediationPlan,
    *,
    tools_by_name: dict[str, Any],
) -> None:
    """Execute the approved plan steps directly through the token-gated tools.

    Used as the scripted-mode path and as the fallback when the commander
    model failed to perform an approved step. Every call still redeems its
    approval token, so audit/policy paths are identical.
    """
    for op, params in plan_ops(plan):
        tool = tools_by_name.get(op)
        if tool is None:
            raise BackendOperationError(op, plan.target_service, "no tool bound for operation")
        kwargs = _op_kwargs(op, plan.target_service, resolve_params(plan, params, ctx))
        await tool.ainvoke(kwargs)


def _op_kwargs(operation: str, target: str, params: dict[str, Any]) -> dict[str, Any]:
    """Map normalized params to the mutating tool's signature."""
    if operation == "patch_runtime_config":
        return {
            "service_name": target,
            "changes": params.get("changes", {}),
            "expected_config_hash": params.get("expected_config_hash", ""),
        }
    if operation == "restore_runtime_config":
        return {"service_name": target, "backup_id": params.get("backup_id", "")}
    return {"service_name": target}


def record_approval_request(
    state: IncidentState,
    plan: RemediationPlan,
    *,
    action_id: str,
    operation: str,
    target: str,
    params: dict[str, Any],
    status: ApprovalStatus,
    approver: str | None = None,
    reason: str | None = None,
) -> ApprovalRequest:
    req = ApprovalRequest(
        approval_id=f"appr-{action_id}",
        incident_id=state.incident_id,
        action_id=action_id,
        plan_id=plan.plan_id,
        operation=operation,
        target=target,
        params_redacted=params,
        requested_at=datetime.now(UTC),
        status=status,
        decided_at=datetime.now(UTC) if status != ApprovalStatus.PENDING else None,
        approver=approver,
        decision_reason=reason,
    )
    state.approval_requests.append(req)
    return req


NodeFn = Callable[[GraphState, RunnableConfig], Awaitable[GraphState]]


def wrap_node_errors[F: NodeFn](name: str, fn: F) -> F:
    """Convert terminal node failures into safe routing signals.

    Budget/approval/structured-output failures escalate; anything else marks
    the incident FAILED if a legal edge exists. The wrapper keeps the graph
    itself from crashing so a report is always produced.
    """

    async def inner(state: GraphState, config: RunnableConfig) -> GraphState:
        try:
            return await fn(state, config)
        except (BudgetExceededError, ApprovalError, StructuredOutputError, PolicyDeniedError) as exc:
            ctx = get_ctx(config)
            incident = bind_state(ctx, state)
            incident.escalation_reason = f"{name}: {exc}"
            ctx.audit_event("node_failure", node=name, error=str(exc))
            return {"next_node": "escalate", "error": str(exc)}
        except Exception as exc:  # unexpected — mark FAILED if we can
            ctx = get_ctx(config)
            incident = bind_state(ctx, state)
            ctx.audit_event("node_crash", node=name, error=str(exc))
            try:
                move(
                    incident,
                    IncidentStatus.FAILED,
                    reason=f"{name} crashed: {exc.__class__.__name__}",
                    actor=f"node:{name}",
                )
            except Exception:
                incident.escalation_reason = f"{name} crashed: {exc}"
            return {"next_node": "report", "error": str(exc)}

    inner.__name__ = name
    return cast(F, inner)


def incident_briefing(state: IncidentState) -> str:
    """HumanMessage body presenting the alert to the investigator."""
    return json.dumps(
        {
            "incident_id": state.incident_id,
            "title": state.title,
            "description": state.description,
            "severity_hint": state.severity.value,
            "affected_services": state.affected_services,
            "constraints": state.constraints,
            "instruction": (
                "Investigate: gather evidence with read-only tools, then "
                "produce the structured InvestigationResult."
            ),
        },
        default=str,
    )
