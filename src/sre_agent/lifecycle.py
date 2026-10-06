"""Explicit incident lifecycle state machine.

The LangGraph workflow drives these transitions, but legality is enforced here
in deterministic code — a node (or a misbehaving model) cannot move the
incident to a status that is not reachable from the current one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sre_agent.exceptions import IllegalTransitionError
from sre_agent.models.incident import IncidentRequest, IncidentState, IncidentStatus, StateTransition

TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.RECEIVED: frozenset({IncidentStatus.TRIAGING, IncidentStatus.FAILED}),
    IncidentStatus.TRIAGING: frozenset(
        {IncidentStatus.INVESTIGATING, IncidentStatus.ESCALATED, IncidentStatus.FAILED}
    ),
    IncidentStatus.INVESTIGATING: frozenset(
        {
            IncidentStatus.PLANNING_REMEDIATION,
            IncidentStatus.ESCALATED,
            IncidentStatus.FAILED,
        }
    ),
    IncidentStatus.PLANNING_REMEDIATION: frozenset(
        {
            IncidentStatus.AWAITING_APPROVAL,
            IncidentStatus.REMEDIATING,
            IncidentStatus.ESCALATED,
            IncidentStatus.FAILED,
        }
    ),
    IncidentStatus.AWAITING_APPROVAL: frozenset(
        {IncidentStatus.REMEDIATING, IncidentStatus.ESCALATED, IncidentStatus.FAILED}
    ),
    IncidentStatus.REMEDIATING: frozenset(
        {
            IncidentStatus.VERIFYING,
            IncidentStatus.ROLLING_BACK,
            IncidentStatus.ESCALATED,
            IncidentStatus.FAILED,
        }
    ),
    IncidentStatus.VERIFYING: frozenset(
        {
            IncidentStatus.RESOLVED,
            IncidentStatus.ROLLING_BACK,
            IncidentStatus.ESCALATED,
            IncidentStatus.FAILED,
        }
    ),
    IncidentStatus.ROLLING_BACK: frozenset({IncidentStatus.ROLLED_BACK, IncidentStatus.FAILED}),
    IncidentStatus.ROLLED_BACK: frozenset({IncidentStatus.ESCALATED}),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.MITIGATED: frozenset(),
    IncidentStatus.ESCALATED: frozenset(),
    IncidentStatus.FAILED: frozenset(),
}

TERMINAL_STATUSES = frozenset(
    {
        IncidentStatus.RESOLVED,
        IncidentStatus.MITIGATED,
        IncidentStatus.ESCALATED,
        IncidentStatus.FAILED,
    }
)

HAPPY_PATH = (
    IncidentStatus.RECEIVED,
    IncidentStatus.TRIAGING,
    IncidentStatus.INVESTIGATING,
    IncidentStatus.PLANNING_REMEDIATION,
    IncidentStatus.REMEDIATING,
    IncidentStatus.VERIFYING,
    IncidentStatus.RESOLVED,
)


def is_terminal(status: IncidentStatus) -> bool:
    return status in TERMINAL_STATUSES


def is_legal_transition(previous: IncidentStatus, new: IncidentStatus) -> bool:
    return new in TRANSITIONS[previous]


def assert_legal_transition(previous: IncidentStatus, new: IncidentStatus) -> None:
    if not is_legal_transition(previous, new):
        raise IllegalTransitionError(previous.value, new.value)


def transition(
    state: IncidentState,
    to: IncidentStatus,
    *,
    reason: str,
    actor: str,
    run_id: str | None = None,
) -> StateTransition:
    """Move ``state`` to ``to``, recording the transition.

    Raises IllegalTransitionError if the edge does not exist in TRANSITIONS.
    """
    assert_legal_transition(state.status, to)
    record = StateTransition(
        incident_id=state.incident_id,
        previous_status=state.status,
        new_status=to,
        timestamp=datetime.now(UTC),
        reason=reason,
        actor=actor,
        run_id=run_id,
    )
    state.status = to
    state.updated_at = record.timestamp
    state.transitions.append(record)
    return record


def new_incident_state(
    request: IncidentRequest,
    *,
    run_mode: str,
    approval_mode: str,
) -> IncidentState:
    """Create a fresh IncidentState in RECEIVED status."""
    now = datetime.now(UTC)
    return IncidentState(
        incident_id=request.incident_id,
        status=IncidentStatus.RECEIVED,
        severity=request.severity_hint,
        title=request.title,
        description=request.description,
        started_at=now,
        updated_at=now,
        affected_services=request.affected_services,
        environment=request.environment,
        run_mode=run_mode,
        approval_mode=approval_mode,
        constraints=request.constraints,
    )
