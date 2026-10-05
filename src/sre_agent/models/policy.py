"""Policy, approval, and safety-event models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    PROHIBITED = "PROHIBITED"


class PolicyDecision(BaseModel):
    """Output of the deterministic PolicyEngine for one proposed action."""

    allowed: bool
    risk_level: RiskLevel
    requires_approval: bool
    reason: str
    required_evidence_count: int = 0
    rollback_required: bool = False
    policy_rule_ids: list[str] = Field(default_factory=list)


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class ApprovalToken(BaseModel, frozen=True):
    """Single-use, scoped, expiring authorization for one mutating action.

    ``signature`` is an HMAC-SHA256 over the canonical token payload produced
    by policy.approvals with a per-run secret. Tokens are never valid for a
    different operation/target/parameter set than they were issued for.
    """

    token_id: str
    incident_id: str
    action_id: str
    operation: str
    target: str
    params_sha256: str
    expires_at: datetime
    issued_at: datetime
    issued_by: str  # "auto-policy:auto_safe" | "human:<id>"
    signature: str


class ApprovalRequest(BaseModel):
    """A recorded request for approval of a proposed action."""

    approval_id: str
    incident_id: str
    action_id: str
    plan_id: str
    operation: str
    target: str
    params_redacted: dict[str, Any]
    requested_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: datetime | None = None
    approver: str | None = None
    decision_reason: str | None = None


class SafetyEventKind(StrEnum):
    POLICY_DENIAL = "POLICY_DENIAL"
    PROHIBITED_ATTEMPT = "PROHIBITED_ATTEMPT"
    TOKEN_MISMATCH = "TOKEN_MISMATCH"
    TOKEN_REPLAY = "TOKEN_REPLAY"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID_STRUCTURED_OUTPUT = "INVALID_STRUCTURED_OUTPUT"
    REDACTION_APPLIED = "REDACTION_APPLIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ROLLBACK_EXECUTED = "ROLLBACK_EXECUTED"
    FORBIDDEN_PATH_ACCESS = "FORBIDDEN_PATH_ACCESS"


class SafetyEvent(BaseModel):
    event_id: str
    incident_id: str
    kind: SafetyEventKind
    detail: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
