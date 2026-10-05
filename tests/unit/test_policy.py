"""PolicyEngine unit tests — decisions must be deterministic and fail-closed."""

from sre_agent.config import load_policy_config
from sre_agent.models import (
    CheckType,
    EvidenceRecord,
    EvidenceSourceType,
    IncidentState,
    IncidentStatus,
    RemediationPlan,
    RiskLevel,
    VerificationStep,
)
from sre_agent.policy.engine import PolicyEngine


def _plan(**kw) -> RemediationPlan:
    base: dict = {
        "incident_id": "INC-1",
        "root_cause": "checkout configured with wrong inventory endpoint",
        "root_cause_confidence": 0.9,
        "supporting_evidence_ids": ["e1", "e2"],
        "target_service": "checkout-service",
        "operation": "patch_runtime_config",
        "normalized_parameters": {
            "changes": {"inventory_url": "http://127.0.0.1:8082"},
            "expected_config_hash": "abc",
        },
        "expected_result": "checkout healthy",
        "verification_steps": [
            VerificationStep(
                check_type=CheckType.SYNTHETIC,
                target="checkout",
                expected="pass",
            )
        ],
        "rollback_operation": "restore_runtime_config",
        "rollback_parameters": {"backup_id": "X"},
    }
    base.update(kw)
    return RemediationPlan(**base)


def _state(evidence_ids=("e1", "e2"), scenario=None) -> IncidentState:
    s = IncidentState(
        incident_id="INC-1",
        status=IncidentStatus.PLANNING_REMEDIATION,
        scenario_id=scenario,
    )
    for eid in evidence_ids:
        s.evidence.append(
            EvidenceRecord(
                evidence_id=eid,
                incident_id="INC-1",
                source_type=EvidenceSourceType.LOG,
                source_name="checkout-service",
                tool_name="search_logs",
                summary="dep failure",
            )
        )
    return s


def _engine(mode="auto_safe") -> PolicyEngine:
    return PolicyEngine(load_policy_config(), run_mode="benchmark", approval_mode=mode)


def test_medium_patch_auto_approved_in_auto_safe():
    d = _engine("auto_safe").evaluate(_plan(), _state())
    assert d.allowed and not d.requires_approval
    assert d.risk_level == RiskLevel.MEDIUM
    assert d.rollback_required


def test_manual_mode_requires_approval():
    d = _engine("manual").evaluate(_plan(), _state())
    assert d.allowed and d.requires_approval


def test_deny_mode_blocks_everything():
    d = _engine("deny").evaluate(_plan(), _state())
    assert not d.allowed


def test_low_risk_restart_allowed():
    plan = _plan(
        operation="restart_service",
        normalized_parameters={},
        rollback_operation=None,
    )
    d = _engine("auto_safe").evaluate(plan, _state())
    assert d.allowed and d.risk_level == RiskLevel.LOW


def test_prohibited_op_denied():
    d = _engine().evaluate(_plan(operation="arbitrary_shell"), _state())
    assert not d.allowed and d.risk_level == RiskLevel.PROHIBITED


def test_unknown_op_fail_closed():
    d = _engine().evaluate(_plan(operation="format_disk"), _state())
    assert not d.allowed


def test_insufficient_evidence_denied():
    plan = _plan(supporting_evidence_ids=["e1"])  # only 1 of required 2
    d = _engine().evaluate(plan, _state())
    assert not d.allowed and "evidence" in d.reason


def test_evidence_must_exist_in_state():
    plan = _plan(supporting_evidence_ids=["ghost-1", "ghost-2"])
    d = _engine().evaluate(plan, _state(evidence_ids=()))
    assert not d.allowed


def test_low_confidence_denied():
    plan = _plan(root_cause_confidence=0.4)
    d = _engine().evaluate(plan, _state())
    assert not d.allowed and "confidence" in d.reason


def test_scenario_target_restriction():
    plan = _plan(target_service="inventory-service")
    d = _engine().evaluate(plan, _state(scenario="wrong-inventory-endpoint"))
    assert not d.allowed


def test_scenario_op_restriction():
    plan = _plan(operation="start_service", normalized_parameters={})
    d = _engine().evaluate(plan, _state(scenario="wrong-inventory-endpoint"))
    assert not d.allowed  # start not in scenario's expected_operations


def test_rollback_always_allowed():
    plan = _plan(
        operation="restore_runtime_config",
        normalized_parameters={"backup_id": "b1"},
        rollback_operation=None,
        supporting_evidence_ids=[],
        root_cause_confidence=0.1,
    )
    d = _engine("auto_safe").evaluate(plan, _state(evidence_ids=()))
    assert d.allowed


def test_missing_rollback_plan_denied():
    plan = _plan(rollback_operation=None, rollback_parameters={})
    d = _engine("auto_safe").evaluate(plan, _state())
    assert not d.allowed and "rollback" in d.reason
