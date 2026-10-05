"""SQLAlchemy schema for incident persistence (spec section 18).

incidents  — one row per incident; the full IncidentState JSON is the source
             of truth, with indexed columns for query/status reports.
incident_events — append-only event mirror (transitions, audit highlights).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IncidentRow(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    scenario_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    environment: Mapped[str] = mapped_column(String(32), default="local")
    run_mode: Mapped[str] = mapped_column(String(16), default="demo")
    approval_mode: Mapped[str] = mapped_column(String(16), default="manual")
    report_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state_json: Mapped[str] = mapped_column(Text)


class IncidentEventRow(Base):
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)  # transition|audit|safety
    actor: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
