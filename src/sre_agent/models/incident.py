"""Incident request, state, transitions, and budget counters."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from sre_agent.models.evidence import EvidenceRecord, Hypothesis
from sre_agent.models.policy import ApprovalRequest, SafetyEvent
from sre_agent.models.remediation import (
    ActionRecord,
    RemediationPlan,
    VerificationResult,
)


class IncidentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    TRIAGING = "TRIAGING"
    INVESTIGATING = "INVESTIGATING"
    PLANNING_REMEDIATION = "PLANNING_REMEDIATION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATING = "REMEDIATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    MITIGATED = "MITIGATED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class IncidentRequest(BaseModel):
    """The public alert handed to the agent — never contains ground truth."""

    incident_id: str
    title: str
    description: str
    reported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    affected_services: list[str] = Field(default_factory=list)
    severity_hint: Severity = Severity.UNKNOWN
    environment: str = "local"
    constraints: list[str] = Field(default_factory=list)
    scenario_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StateTransition(BaseModel, frozen=True):
    incident_id: str
    previous_status: IncidentStatus
    new_status: IncidentStatus
    timestamp: datetime
    reason: str
    actor: str  # node name or "human:<id>"
    run_id: str | None = None


class BudgetCounters(BaseModel):
    """Mutable counters; limits live in Settings, enforcement in graph/budgets."""

    model_turns: int = 0
    tool_calls: int = 0
    remediation_attempts: int = 0
    verification_retries: int = 0
    no_progress_cycles: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0


class IncidentState(BaseModel):
    """Authoritative incident record. Carried through the LangGraph state."""

    incident_id: str
    status: IncidentStatus
    severity: Severity = Severity.UNKNOWN
    title: str = ""
    description: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    affected_services: list[str] = Field(default_factory=list)
    environment: str = "local"
    run_mode: str = "demo"
    approval_mode: str = "manual"
    constraints: list[str] = Field(default_factory=list)
    scenario_id: str | None = None

    evidence: list[EvidenceRecord] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    proposed_actions: list[RemediationPlan] = Field(default_factory=list)
    executed_actions: list[ActionRecord] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)
    safety_events: list[SafetyEvent] = Field(default_factory=list)
    transitions: list[StateTransition] = Field(default_factory=list)

    budgets: BudgetCounters = Field(default_factory=BudgetCounters)
    pending_plan: RemediationPlan | None = None
    escalation_reason: str | None = None
    final_report_path: str | None = None
    trace_metadata: dict[str, Any] = Field(default_factory=dict)

    def add_evidence(self, record: EvidenceRecord) -> tuple[EvidenceRecord, bool]:
        """Append evidence unless an identical observation already exists.

        Returns (record, is_new). Deduplication is by content_hash so repeated
        tool reads cannot inflate hypothesis confidence.
        """
        for existing in self.evidence:
            if existing.content_hash == record.content_hash:
                return existing, False
        self.evidence.append(record)
        return record, True

    def evidence_ids(self) -> set[str]:
        return {e.evidence_id for e in self.evidence}

    def active_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status.value == "ACTIVE"]

    def add_safety_event(self, event: SafetyEvent) -> None:
        self.safety_events.append(event)
        self.updated_at = datetime.now(UTC)
