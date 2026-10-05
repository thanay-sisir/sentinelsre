"""IncidentRepository — save/load IncidentState, append events, list runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sre_agent.models.incident import IncidentState
from sre_agent.persistence.tables import IncidentEventRow, IncidentRow


class IncidentRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, state: IncidentState) -> None:
        async with self._sessions() as s:
            row = await s.get(IncidentRow, state.incident_id)
            duration = (state.updated_at - state.started_at).total_seconds()
            payload = {
                "title": state.title,
                "status": state.status.value,
                "severity": state.severity.value,
                "scenario_id": state.scenario_id,
                "environment": state.environment,
                "run_mode": state.run_mode,
                "approval_mode": state.approval_mode,
                "report_path": state.final_report_path,
                "trace_id": state.trace_metadata.get("langsmith_trace_id"),
                "duration_seconds": duration,
                "started_at": state.started_at,
                "updated_at": state.updated_at,
                "state_json": state.model_dump_json(),
            }
            if row is None:
                s.add(IncidentRow(incident_id=state.incident_id, **payload))
            else:
                for k, v in payload.items():
                    setattr(row, k, v)
            await s.commit()

    async def get(self, incident_id: str) -> IncidentState | None:
        async with self._sessions() as s:
            row = await s.get(IncidentRow, incident_id)
            if row is None:
                return None
            return IncidentState.model_validate_json(row.state_json)

    async def list_incidents(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self._sessions() as s:
            res = await s.execute(select(IncidentRow).order_by(desc(IncidentRow.started_at)).limit(limit))
            return [
                {
                    "incident_id": r.incident_id,
                    "title": r.title,
                    "status": r.status,
                    "severity": r.severity,
                    "scenario_id": r.scenario_id,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "duration_seconds": r.duration_seconds,
                    "report_path": r.report_path,
                }
                for r in res.scalars().all()
            ]

    async def record_event(
        self,
        incident_id: str,
        kind: str,
        actor: str,
        detail: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessions() as s:
            s.add(
                IncidentEventRow(
                    incident_id=incident_id,
                    kind=kind,
                    actor=actor,
                    detail=detail[:2000],
                    payload=payload or {},
                    created_at=datetime.now(UTC),
                )
            )
            await s.commit()
