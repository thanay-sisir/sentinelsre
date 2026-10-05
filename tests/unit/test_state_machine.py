"""Unit tests for the incident lifecycle state machine."""

from itertools import pairwise

import pytest

from sre_agent.exceptions import IllegalTransitionError
from sre_agent.lifecycle import (
    HAPPY_PATH,
    is_legal_transition,
    is_terminal,
    new_incident_state,
    transition,
)
from sre_agent.models import IncidentRequest, IncidentStatus, Severity


def _request() -> IncidentRequest:
    return IncidentRequest(
        incident_id="INC-1001",
        title="Checkout 5xx",
        description="Checkout error rate above 50%",
        affected_services=["checkout-service"],
        severity_hint=Severity.HIGH,
    )


def _state():
    return new_incident_state(_request(), run_mode="demo", approval_mode="manual")


def test_happy_path_reaches_resolved():
    state = _state()
    assert state.status == IncidentStatus.RECEIVED
    for prev, nxt in pairwise(HAPPY_PATH):
        assert state.status == prev
        transition(state, nxt, reason="test", actor="test-node")
    assert state.status == IncidentStatus.RESOLVED
    assert is_terminal(state.status)
    assert len(state.transitions) == len(HAPPY_PATH) - 1


def test_illegal_transition_raises():
    state = _state()
    with pytest.raises(IllegalTransitionError):
        transition(state, IncidentStatus.RESOLVED, reason="skip", actor="test")


def test_terminal_state_cannot_transition():
    state = _state()
    for nxt in HAPPY_PATH[1:]:
        transition(state, nxt, reason="t", actor="t")
    with pytest.raises(IllegalTransitionError):
        transition(state, IncidentStatus.INVESTIGATING, reason="reopen", actor="t")


@pytest.mark.parametrize(
    ("prev", "new"),
    [
        (IncidentStatus.INVESTIGATING, IncidentStatus.REMEDIATING),  # skips planning
        (IncidentStatus.RECEIVED, IncidentStatus.VERIFYING),
        (IncidentStatus.ROLLED_BACK, IncidentStatus.RESOLVED),
        (IncidentStatus.AWAITING_APPROVAL, IncidentStatus.VERIFYING),
        (IncidentStatus.REMEDIATING, IncidentStatus.RESOLVED),  # must verify first
    ],
)
def test_transition_table_rejects_shortcuts(prev, new):
    assert not is_legal_transition(prev, new)


def test_rollback_path():
    state = _state()
    for nxt in HAPPY_PATH[1:-1]:
        transition(state, nxt, reason="t", actor="t")
    transition(state, IncidentStatus.ROLLING_BACK, reason="verify failed", actor="verify")
    transition(state, IncidentStatus.ROLLED_BACK, reason="restored", actor="rollback")
    transition(state, IncidentStatus.ESCALATED, reason="needs human", actor="rollback")
    assert state.status == IncidentStatus.ESCALATED
    assert is_terminal(state.status)


def test_transition_records_metadata():
    state = _state()
    rec = transition(state, IncidentStatus.TRIAGING, reason="alert parsed", actor="triage", run_id="r1")
    assert rec.previous_status == IncidentStatus.RECEIVED
    assert rec.new_status == IncidentStatus.TRIAGING
    assert rec.reason == "alert parsed"
    assert rec.actor == "triage"
    assert rec.run_id == "r1"
    assert state.transitions[-1] == rec
