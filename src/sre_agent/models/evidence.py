"""Evidence and hypothesis models.

Evidence is immutable and content-hashed so duplicate observations cannot
artificially inflate hypothesis confidence (spec section 12).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _canonical_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class EvidenceSourceType(StrEnum):
    ALERT = "ALERT"
    SERVICE_STATUS = "SERVICE_STATUS"
    LOG = "LOG"
    METRIC = "METRIC"
    CONFIGURATION = "CONFIGURATION"
    DEPENDENCY = "DEPENDENCY"
    HEALTH_CHECK = "HEALTH_CHECK"
    SYNTHETIC_CHECK = "SYNTHETIC_CHECK"
    RUNBOOK = "RUNBOOK"
    ACTION_RESULT = "ACTION_RESULT"


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    incident_id: str
    source_type: EvidenceSourceType
    source_name: str  # e.g. service name, runbook file, tool target
    tool_name: str
    tool_call_id: str | None = None
    query_or_parameters: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    redacted: bool = False
    content_hash: str = ""

    @model_validator(mode="after")
    def _fill_content_hash(self) -> Self:
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                _canonical_hash(
                    {
                        "source_type": self.source_type.value,
                        "source_name": self.source_name,
                        "data": self.structured_data,
                    }
                ),
            )
        return self


class HypothesisStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    CONFIRMED = "CONFIRMED"


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: uuid4().hex)
    description: str
    candidate_root_cause: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    reasoning_summary: str = ""


class InvestigationResult(BaseModel):
    """Structured output contract of the Investigation node."""

    summary: str
    affected_services: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=3)
    most_likely_hypothesis_id: str | None = None
    recommended_next_step: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    insufficient_evidence_reason: str | None = None
