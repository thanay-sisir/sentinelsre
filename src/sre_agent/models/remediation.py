"""Remediation planning, action, and verification models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from sre_agent.models.policy import PolicyDecision, RiskLevel


class CheckType(StrEnum):
    HEALTH = "health"
    READINESS = "readiness"
    SYNTHETIC = "synthetic"
    METRIC_THRESHOLD = "metric_threshold"
    CONFIG_VALUE = "config_value"
    PROCESS_RUNNING = "process_running"


class VerificationStep(BaseModel):
    """One deterministic check inside a remediation plan."""

    check_type: CheckType
    target: str  # service name or check name
    expected: str  # human-readable expected outcome
    params: dict[str, Any] = Field(default_factory=dict)


class RemediationPlan(BaseModel):
    """Structured output contract of the Remediation Planner node."""

    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    incident_id: str = ""
    root_cause: str
    root_cause_confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    target_service: str
    operation: str  # must be a known mutating op, e.g. patch_runtime_config
    normalized_parameters: dict[str, Any] = Field(default_factory=dict)
    # Optional immediate follow-up step (e.g. reload_service after a patch).
    post_operation: str | None = None
    post_parameters: dict[str, Any] = Field(default_factory=dict)
    expected_result: str
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_approval: bool = True
    verification_steps: list[VerificationStep] = Field(default_factory=list, min_length=1)
    rollback_operation: str | None = None
    rollback_parameters: dict[str, Any] = Field(default_factory=dict)
    justification: str = ""


class ActionRecord(BaseModel):
    """Immutable-style audit record of one executed (or attempted) action."""

    action_id: str = Field(default_factory=lambda: uuid4().hex)
    incident_id: str
    plan_id: str | None = None
    tool_name: str
    target: str
    parameters_redacted: dict[str, Any] = Field(default_factory=dict)
    policy_decision: PolicyDecision | None = None
    approval_reference: str | None = None  # approval_id
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    success: bool | None = None
    result_summary: str = ""
    backend_result: dict[str, Any] = Field(default_factory=dict)
    rollback_reference: str | None = None  # action_id this action rolled back


class VerificationResult(BaseModel):
    check_id: str = Field(default_factory=lambda: uuid4().hex)
    check_type: CheckType
    target: str
    expected: str
    observed: str
    passed: bool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)
