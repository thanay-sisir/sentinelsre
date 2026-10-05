"""Incident report models (JSON schema the verifier validates)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TimelineEntry(BaseModel):
    timestamp: datetime
    actor: str  # agent node, tool, human, system
    event: str
    detail: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class IncidentReport(BaseModel):
    """Structured post-incident report. All claims must trace to state."""

    incident_id: str
    title: str
    final_status: str
    severity: str
    started_at: datetime
    resolved_at: datetime | None = None
    duration_seconds: float | None = None
    affected_services: list[str] = Field(default_factory=list)
    customer_impact: str = ""
    symptoms: list[str] = Field(default_factory=list)
    root_cause: str
    root_cause_confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)  # evidence ids
    timeline: list[TimelineEntry] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)  # action ids + summary
    verification_results: list[dict[str, Any]] = Field(default_factory=list)
    rollback_performed: bool = False
    remaining_risks: list[str] = Field(default_factory=list)
    safety_events: list[str] = Field(default_factory=list)
    escalation_reason: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    agent_version: str = ""
    agent_framework: str = "langgraph"
    prompt_version: str = ""
    model_configuration: dict[str, Any] = Field(default_factory=dict)
    harbor_trial_id: str | None = None
    langsmith_trace_id: str | None = None
