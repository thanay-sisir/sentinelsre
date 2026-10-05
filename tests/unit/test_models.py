"""Unit tests for core Pydantic models."""

import pytest
from pydantic import ValidationError

from sre_agent.models import (
    CheckType,
    EvidenceRecord,
    EvidenceSourceType,
    Hypothesis,
    HypothesisStatus,
    RemediationPlan,
    RiskLevel,
    VerificationStep,
)
from sre_agent.models.incident import IncidentState, IncidentStatus


def _evidence(data: dict, **kw) -> EvidenceRecord:
    return EvidenceRecord(
        incident_id="INC-1",
        source_type=EvidenceSourceType.LOG,
        source_name="checkout-service",
        tool_name="get_recent_logs",
        summary="s",
        structured_data=data,
        **kw,
    )


def test_evidence_content_hash_deterministic():
    a = _evidence({"x": 1, "y": [1, 2]})
    b = _evidence({"y": [1, 2], "x": 1})  # key order differs
    assert a.content_hash == b.content_hash


def test_evidence_dedup_by_content_hash():
    state = IncidentState(incident_id="INC-1", status=IncidentStatus.INVESTIGATING)
    e1 = _evidence({"f": "a"})
    e2 = _evidence({"f": "a"})  # duplicate observation
    e3 = _evidence({"f": "b"})
    assert state.add_evidence(e1)[1] is True
    rec, is_new = state.add_evidence(e2)
    assert is_new is False
    assert rec.evidence_id == e1.evidence_id  # returns the original
    assert state.add_evidence(e3)[1] is True
    assert len(state.evidence) == 2


def test_hypothesis_confidence_bounds():
    Hypothesis(description="d", candidate_root_cause="c", confidence=0.5)
    with pytest.raises(ValidationError):
        Hypothesis(description="d", candidate_root_cause="c", confidence=1.5)
    with pytest.raises(ValidationError):
        Hypothesis(description="d", candidate_root_cause="c", confidence=-0.1)


def test_active_hypotheses_filter():
    state = IncidentState(incident_id="i", status=IncidentStatus.INVESTIGATING)
    state.hypotheses.append(Hypothesis(description="a", candidate_root_cause="x", confidence=0.4))
    state.hypotheses.append(
        Hypothesis(
            description="b",
            candidate_root_cause="y",
            confidence=0.9,
            status=HypothesisStatus.CONFIRMED,
        )
    )
    assert [h.description for h in state.active_hypotheses()] == ["a"]


def test_remediation_plan_requires_verification_steps():
    with pytest.raises(ValidationError):
        RemediationPlan(
            root_cause="bad endpoint",
            root_cause_confidence=0.9,
            target_service="checkout-service",
            operation="patch_runtime_config",
            expected_result="ok",
            verification_steps=[],
        )
    plan = RemediationPlan(
        root_cause="bad endpoint",
        root_cause_confidence=0.9,
        target_service="checkout-service",
        operation="patch_runtime_config",
        expected_result="ok",
        verification_steps=[
            VerificationStep(
                check_type=CheckType.SYNTHETIC,
                target="checkout",
                expected="transaction succeeds",
            )
        ],
    )
    assert plan.risk_level == RiskLevel.MEDIUM


def test_evidence_record_is_frozen():
    e = _evidence({"a": 1})
    with pytest.raises(ValidationError):
        e.summary = "mutated"  # type: ignore[misc]
