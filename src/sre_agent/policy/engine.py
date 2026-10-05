"""Deterministic policy engine — code, not prompts, decides what may run.

Every mutating action gets a PolicyDecision BEFORE the backend is touched.
Fail-closed: operations not in the policy table are denied.
"""

from __future__ import annotations

from sre_agent.config import PolicyConfig
from sre_agent.models import (
    IncidentState,
    PolicyDecision,
    RemediationPlan,
    RiskLevel,
)

_RISK_ORDER = [
    RiskLevel.READ_ONLY,
    RiskLevel.LOW,
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.PROHIBITED,
]


class PolicyEngine:
    def __init__(self, config: PolicyConfig, *, run_mode: str, approval_mode: str):
        self._cfg = config
        self._run_mode = run_mode
        self._approval_mode = approval_mode

    def _deny(self, risk: RiskLevel, reason: str, rules: list[str]) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            risk_level=risk,
            requires_approval=False,
            reason=reason,
            policy_rule_ids=rules,
        )

    def evaluate(self, plan: RemediationPlan, state: IncidentState) -> PolicyDecision:
        cfg = self._cfg
        rules: list[str] = []
        op = plan.operation
        rules.append(f"op.{op}")

        # 1. Prohibited operations — always denied + safety event upstream.
        if cfg.is_prohibited(op):
            return self._deny(RiskLevel.PROHIBITED, f"operation {op!r} is prohibited", rules)

        # 2. Unknown mutating operation — fail closed.
        op_policy = cfg.operation(op)
        if op_policy is None:
            return self._deny(
                RiskLevel.HIGH,
                f"operation {op!r} is not in the policy allowlist",
                rules,
            )

        risk = RiskLevel(op_policy.risk)
        rollback_required = cfg.rollback_required_for(op)
        if rollback_required:
            rules.append("defaults.rollback_required")

        # 3. Scenario-scoped allowlists.
        if state.scenario_id and state.scenario_id in cfg.scenario_overrides:
            override = cfg.scenario_overrides[state.scenario_id]
            rules.append(f"scenario.{state.scenario_id}")
            expected = override.get("expected_operations")
            targets = override.get("acceptable_targets")
            if expected and op not in expected:
                return self._deny(
                    risk,
                    f"operation {op!r} outside scenario expected_operations",
                    rules,
                )
            if targets and plan.target_service not in targets:
                return self._deny(
                    risk,
                    f"target {plan.target_service!r} outside scenario acceptable_targets",
                    rules,
                )

        # 4. Rollback ops are always allowed (rollback must never be blocked),
        #    and never wait on human approval even in manual mode.
        if op_policy.is_rollback:
            rules.append("op.is_rollback")
            return PolicyDecision(
                allowed=True,
                risk_level=risk,
                requires_approval=False,
                reason="rollback operation — allowed to restore prior state",
                required_evidence_count=0,
                rollback_required=False,
                policy_rule_ids=rules,
            )

        # 5. Evidence + confidence gates for MEDIUM+.
        threshold = cfg.confidence_threshold()
        if risk in (RiskLevel.MEDIUM, RiskLevel.HIGH):
            min_ev = cfg.min_evidence_for_medium_risk()
            rules.append(f"evidence.min{min_ev}")
            known_ids = state.evidence_ids()
            cited = [e for e in plan.supporting_evidence_ids if e in known_ids]
            if len(cited) < min_ev:
                return self._deny(
                    risk,
                    f"insufficient grounded evidence: {len(cited)} of "
                    f"{min_ev} required evidence ids exist in incident state",
                    rules,
                )
            if plan.root_cause_confidence < threshold:
                return self._deny(
                    risk,
                    f"confidence {plan.root_cause_confidence:.2f} below threshold {threshold:.2f}",
                    rules + [f"confidence>={threshold}"],
                )

        # 6. Rollback plan presence for state-changing ops.
        if rollback_required and not plan.rollback_operation:
            return self._deny(risk, "policy requires a rollback plan for this operation", rules)

        # 7. Approval requirements by mode.
        requires_approval = self._approval_mode == "manual"
        if self._approval_mode == "deny":
            return self._deny(risk, "approval_mode=deny blocks all mutations", rules)
        if self._approval_mode == "auto_safe":
            rules.append("mode.auto_safe")
            if not op_policy.auto_approve_in_benchmark:
                return self._deny(
                    risk,
                    f"{op!r} is not auto-approvable under auto_safe policy",
                    rules,
                )

        return PolicyDecision(
            allowed=True,
            risk_level=risk,
            requires_approval=requires_approval,
            reason=f"{op!r} permitted at {risk.value} risk (mode={self._approval_mode})",
            required_evidence_count=(
                cfg.min_evidence_for_medium_risk() if risk != RiskLevel.READ_ONLY else 0
            ),
            rollback_required=rollback_required,
            policy_rule_ids=rules,
        )
